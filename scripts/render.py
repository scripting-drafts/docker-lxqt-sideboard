#!/usr/bin/env python3
"""Render source-controlled Kubernetes resources from user-editable non-secret settings."""
import json
import pathlib
import re
import sys
try:
    import yaml
except ImportError:
    sys.exit('Install python3-yaml first: sudo apt install python3-yaml')
ROOT=pathlib.Path(__file__).resolve().parent.parent
CONFIG=ROOT/'config'/'settings.json'
OUT=ROOT/'generated'

def env(**entries):
    return [{'name': k, 'value': str(v).lower() if isinstance(v,bool) else str(v)} for k,v in entries.items()]

def volume_mount(name,path,read_only=False,sub_path=None):
    v={'name':name,'mountPath':path,'readOnly':read_only}
    if sub_path: v['subPath']=sub_path
    return v

def load():
    if not CONFIG.is_file():
        sys.exit('Missing config/settings.json; run python3 scripts/configure.py')
    s=json.loads(CONFIG.read_text())
    if not pathlib.Path(s['host_data_dir']).is_absolute():
        sys.exit('host_data_dir must be an absolute path')
    if not re.fullmatch(r'[0-9,\-]+',s['host_cpu_set']): sys.exit('Invalid cpuset')
    cpus=set()
    for item in s['host_cpu_set'].split(','):
        a,*b=item.split('-'); start=int(a); end=int(b[0]) if b else start
        if start>end or end>2048: sys.exit('Invalid CPU range')
        cpus.update(range(start,end+1))
    if not cpus or 2 in cpus or 8 in cpus: sys.exit('CPU 2 and 8 must be excluded')
    for key in ('cluster','namespace','tailscale_hostname'):
        if not re.fullmatch(r'[a-z0-9]([-a-z0-9]*[a-z0-9])?',s[key]): sys.exit(f'Bad {key}')
    if not re.fullmatch(r'[0-9]{3,5}x[0-9]{3,5}x(16|24|32)',s['resolution']):sys.exit('Bad resolution')
    return s

def main():
    s=load(); OUT.mkdir(exist_ok=True)
    cluster=s['cluster']; ns=s['namespace']; node=cluster+'-control-plane'
    kind={'kind':'Cluster','apiVersion':'kind.x-k8s.io/v1alpha4','networking':{'apiServerAddress':'127.0.0.1'},'nodes':[{'role':'control-plane','extraMounts':[{'hostPath':s['host_data_dir'],'containerPath':'/var/local/lxqt-data'},{'hostPath':'/dev/net/tun','containerPath':'/dev/net/tun'}]}]}
    (OUT/'kind.yaml').write_text(yaml.safe_dump(kind,sort_keys=False))
    resources=[{'apiVersion':'v1','kind':'Namespace','metadata':{'name':ns,'labels':{'app.kubernetes.io/part-of':'lxqt-kind-secure'}}}]
    for label,claim,capacity,dir_name in [('home','desktop-home',s['desktop_storage_gib'],'home'),('tailscale','tailscale-state',s['tailscale_storage_gib'],'tailscale')]:
        pv_name=f'{ns}-{label}-pv'
        resources.append({'apiVersion':'v1','kind':'PersistentVolume','metadata':{'name':pv_name,'labels':{'app.kubernetes.io/part-of':'lxqt-kind-secure'}},'spec':{'capacity':{'storage':f'{capacity}Gi'},'accessModes':['ReadWriteOnce'],'persistentVolumeReclaimPolicy':'Retain','storageClassName':'','volumeMode':'Filesystem','hostPath':{'path':'/var/local/lxqt-data/'+dir_name,'type':'Directory'},'nodeAffinity':{'required':{'nodeSelectorTerms':[{'matchExpressions':[{'key':'kubernetes.io/hostname','operator':'In','values':[node]}]}]}}}})
        resources.append({'apiVersion':'v1','kind':'PersistentVolumeClaim','metadata':{'name':claim,'namespace':ns},'spec':{'accessModes':['ReadWriteOnce'],'resources':{'requests':{'storage':f'{capacity}Gi'}},'storageClassName':'','volumeName':pv_name}})
    all_caps={'drop':['ALL']}; sec={'allowPrivilegeEscalation':False,'capabilities':all_caps,'readOnlyRootFilesystem':True,'seccompProfile':{'type':'RuntimeDefault'}}
    secret_volumes=[{'name':vol,'secret':{'secretName':'lxqt-runtime-secrets','defaultMode':292,'items':[{'key':key,'path':key}]}} for vol,key in [('wg-secret','wireguard_private_key'),('vnc-secret','vnc_password'),('tailscale-secret','tailscale_authkey'),('caddy-secret','Caddyfile')]] # 0444
    gluetun={'name':'gluetun','image':s['gluetun_image'],'imagePullPolicy':'IfNotPresent','restartPolicy':'Always',
              'securityContext':{'runAsUser':0,'runAsGroup':0,'allowPrivilegeEscalation':False,'capabilities':{'drop':['ALL'],'add':['NET_ADMIN']},'seccompProfile':{'type':'RuntimeDefault'}},
              # WireGuard Gluetun already runs as UID/GID 0. Match optional file
              # ownership to that identity, so chown needs no extra capability.
              # Avoid v3.40.0's non-searchable /tmp/gluetun directory. Do not
              # disable reporting: old versions ignore PUBLICIP_ENABLED, and
              # even newer versions still write the status file during cleanup.
              # Independent desktop egress tests, VPN probes and firewall stay on.
              'env':env(VPN_SERVICE_PROVIDER='protonvpn',VPN_TYPE='wireguard',FREE_ONLY='on' if s['proton_free_only'] else 'off',VPN_PORT_FORWARDING='off',WIREGUARD_PRIVATE_KEY_SECRETFILE='/run/secrets/wireguard_private_key',DNS_KEEP_NAMESERVER='off',DNS_SERVER='on',PUID=0,PGID=0,PUBLICIP_FILE='/tmp/gluetun-ip'),
             'volumeMounts':[volume_mount('tun','/dev/net/tun'),volume_mount('wg-secret','/run/secrets',True),volume_mount('gluetun-data','/gluetun')],
             'startupProbe':{'exec':{'command':['/gluetun-entrypoint','healthcheck']},'periodSeconds':5,'failureThreshold':90,'timeoutSeconds':4},
             'readinessProbe':{'exec':{'command':['/gluetun-entrypoint','healthcheck']},'periodSeconds':15,'failureThreshold':2,'timeoutSeconds':4}}
    if s.get('proton_server_countries'): gluetun['env'].append({'name':'SERVER_COUNTRIES','value':s['proton_server_countries']})
    desktop={'name':'desktop','image':s['desktop_image'],'imagePullPolicy':'IfNotPresent',
             'securityContext':dict(sec,runAsUser=1000,runAsGroup=1000),
             'env':env(DISPLAY=':99',RESOLUTION=s['resolution'],MULTIPTR='1' if s['multi_pointer'] else '0'),
             'volumeMounts':[volume_mount('desktop-home','/home/desktop'),volume_mount('vnc-secret','/run/secrets',True),volume_mount('tmp','/tmp'),volume_mount('run','/run'),volume_mount('shm','/dev/shm')],
              'startupProbe':{'exec':{'command':['/bin/sh','-c','curl -fsS --max-time 2 http://127.0.0.1:6080/vnc.html >/dev/null && /usr/local/bin/check-vnc --timeout 2']},'periodSeconds':5,'failureThreshold':60,'timeoutSeconds':5},
              'readinessProbe':{'exec':{'command':['/bin/sh','-c','curl -fsS --max-time 2 http://127.0.0.1:6080/vnc.html >/dev/null && /usr/local/bin/check-vnc --timeout 2']},'periodSeconds':15,'timeoutSeconds':5},
             'resources':{'requests':{'memory':'512Mi','cpu':'250m'},'limits':{'memory':'3Gi'}}}
    caddy={'name':'caddy','image':s['caddy_image'],'imagePullPolicy':'IfNotPresent',
           # The official caddy:2 executable has cap_net_bind_service=+ep. Dropping
           # every capability from the bounding set prevents execve with EPERM
           # even when Caddy would only listen on an unprivileged port.
           # Retain just this capability; keep non-root, no-new-privileges,
           # read-only root FS, and RuntimeDefault seccomp.
           'securityContext':dict(sec,runAsUser=1000,runAsGroup=1000,
                                  capabilities={'drop':['ALL'],'add':['NET_BIND_SERVICE']}),
           'command':['caddy','run','--config','/etc/caddy/Caddyfile','--adapter','caddyfile'],
           'env':env(XDG_DATA_HOME='/tmp/caddy-data',XDG_CONFIG_HOME='/tmp/caddy-config'),
           'volumeMounts':[volume_mount('caddy-secret','/etc/caddy',True),volume_mount('tmp','/tmp')],
           'resources':{'requests':{'memory':'64Mi','cpu':'50m'},'limits':{'memory':'256Mi'}}}
    tailscale={'name':'tailscale','image':s['tailscale_image'],'imagePullPolicy':'IfNotPresent',
               'securityContext':dict(sec,readOnlyRootFilesystem=False,runAsUser=0),
               'env':env(TS_AUTHKEY='file:/run/secrets/tailscale_authkey',TS_AUTH_ONCE='true',TS_USERSPACE='true',TS_KUBE_SECRET='',TS_STATE_DIR='/var/lib/tailscale',TS_HOSTNAME=s['tailscale_hostname'],TS_ACCEPT_DNS='false'),
               'volumeMounts':[volume_mount('tailscale-secret','/run/secrets',True),volume_mount('tailscale-state','/var/lib/tailscale'),volume_mount('ts-run','/var/run/tailscale')],
               'resources':{'requests':{'memory':'64Mi','cpu':'50m'},'limits':{'memory':'512Mi'}}}
    deploy={'apiVersion':'apps/v1','kind':'Deployment','metadata':{'name':'lxqt-desktop','namespace':ns,'labels':{'app':'lxqt-desktop'}},
            'spec':{'replicas':1,'revisionHistoryLimit':2,'progressDeadlineSeconds':1200,'strategy':{'type':'Recreate'},'selector':{'matchLabels':{'app':'lxqt-desktop'}},
                    'template':{'metadata':{'labels':{'app':'lxqt-desktop'}},
                                'spec':{'automountServiceAccountToken':False,'hostNetwork':False,'hostPID':False,'hostIPC':False,
                                         # This VPN-isolated Pod does not use cluster Services.
                                         # ClusterFirst adds ndots:5/search domains whose DNS
                                         # forwarding is blocked by the VPN firewall, delaying
                                         # ordinary public lookups and flapping health probes.
                                         'nodeSelector':{'kubernetes.io/hostname':node},'dnsPolicy':'None',
                                         'dnsConfig':{'nameservers':['127.0.0.1'],'searches':[],
                                                      'options':[{'name':'ndots','value':'1'},{'name':'timeout','value':'2'},{'name':'attempts','value':'2'}]},
                                        'securityContext':{'seccompProfile':{'type':'RuntimeDefault'}},
                                        'initContainers':[gluetun], 'containers':[desktop,caddy,tailscale],
                                        'volumes':[{'name':'tun','hostPath':{'path':'/dev/net/tun','type':'CharDevice'}},
                                                   *secret_volumes,{'name':'desktop-home','persistentVolumeClaim':{'claimName':'desktop-home'}},
                                                   {'name':'tailscale-state','persistentVolumeClaim':{'claimName':'tailscale-state'}},
                                                   {'name':'tmp','emptyDir':{'medium':'Memory','sizeLimit':'512Mi'}},
                                                   {'name':'run','emptyDir':{'medium':'Memory','sizeLimit':'32Mi'}},
                                                   {'name':'shm','emptyDir':{'medium':'Memory','sizeLimit':'1Gi'}},
                                                   {'name':'ts-run','emptyDir':{}},{'name':'gluetun-data','emptyDir':{}}]}}}}
    resources.append(deploy)
    (OUT/'workload.yaml').write_text(yaml.safe_dump_all(resources,sort_keys=False,explicit_start=True))
    print('Wrote generated/kind.yaml and generated/workload.yaml; no plaintext credentials in manifests.')

if __name__=='__main__': main()
