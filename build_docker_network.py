__author__ = 'apple'
import json, mini_rest, logging

APIC_TOKEN = None
CONTROLLER = 'http://1.1.1.1'
CERT = None


logging.basicConfig(
    format="[%(asctime)s] %(name)s:%(levelname)s: %(message)s"
)

def _build_everything(url=None, body=None):
    a = mini_rest.rest_it()
    a.Controller = CONTROLLER
    a.URI = url
    a.Action = 'POST'
    a.Body = body
    a.APIC_Token = APIC_TOKEN
    a.SCert = CERT
    return a.rest_run()

def _get_everything(url=None):
    a = mini_rest.rest_it()
    a.Controller = CONTROLLER
    a.URI = url
    a.Action = 'GET'
    a.APIC_Token = APIC_TOKEN
    a.SCert = CERT
    return a.rest_run()

class DockerHandler:
    def __init__(self,preconf,input):
        fp = open(preconf, 'r')
        conf = json.load(fp)
        fp.close()

        self.tenant = conf['tenant']
        self.vrf = conf['vrf']
        self.anp = conf['anp']
        self.pod = conf['pod']
        self.vlanpool = [x for x in range(conf['vlan-start'], conf['vlan-stop'])]

        fp = open(input, 'r')
        conf = json.load(fp)
        fp.close()

        self.hostip = conf['hostip']
        self.service = conf['service']
        self.subnet = conf['subnet']

    def _get_leaf(self):
        '''
        Get the leaf ID based on host ip address which connects to it.
        :return: leaf id or '0' for no leaf.
        '''
        url = '/api/class/fvCEp.json?rsp-subtree=full&rsp-subtree-include=required&rsp-subtree-filter=eq(fvIp.addr,"%s")&rsp-prop-include=naming-only'%self.hostip
        result = _get_everything(url)
        if result['totalCount'] == '0':
            return result['totalCount']
        else:
            return result['imdata'][0]['fvCEp']['children'][0]['fvIp']['children'][0]['fvReportingNode']['attributes']['id']

    def _get_epg(self):
        '''
        Check if the epg is exist in this pod.
        :return: epg name or '0' for no epg.
        '''
        url = '/api/class/fvEPg.json?query-target-filter=eq(fvAEPg.name,"%s")&rsp-prop-include=naming-only'%self.service

        result = _get_everything(url)
        if result['totalCount'] == '0':
            return result['totalCount']
        else:
            return result['imdata'][0]['fvAEPg']['attributes']['name']

    def _create_bd_subnet(self):
        '''
        Create BD with input service name and assign configured subnet to it.
        :return: rest result
        '''
        url = '/api/mo/uni/tn-%s.json'%self.tenant
        body = {
                  "fvBD":{
                   "attributes":{
                    "name":self.service,
                    "status":"created"
                    },
                    "children":[
                       {
                       "fvRsCtx":{
                         "attributes":{
                           "tnFvCtxName":self.vrf
                         }
                       }
                       },
                       {
                       "fvSubnet":{
                          "attributes":{
                            "ip":self.subnet,
                            "scope":"private"
                            }
                        }
                        }
                       ]
                   }
               }
        return _build_everything(url,body)

    def _create_epg(self):
        '''
        Create EPG with input service name
        :return: rest result
        '''
        url = '/api/mo/uni/tn-%s/ap-%s.json' %(self.tenant, self.anp)
        body = {
                 "fvAEPg":{
                   "attributes":{
                     "name":self.service
                   },
                   "children":[
                     {
                       "fvRsBd":{
                         "attributes":{
                           "tnFvBDName":self.service
                         }
                       }
                     }
                   ]
                  }
               }
        return _build_everything(url,body)

    def _get_epg_vlan(self):
        '''
        Get vlan list of all epgs in certain leaf.
        :return: dict of epg/vlan mapping or '0' for no vlan for any epg on the leaf
        '''
        url = '/api/class/fvAp.json?rsp-subtree=full&rsp-subtree-include=required&rsp-subtree-filter=eq(fvRsNodeAtt.tDn,"topology/%s/node-%s")&rsp-prop-include=config-only' %(self.pod, self._get_leaf())
        result = _get_everything(url)
        if result['totalCount'] == '0':
            return result['totalCount']
        mapping = result['imdata'][0]['fvAp']['children']
        epg_vlan_mapping = {}
        for i in mapping:
            epg_vlan_mapping[i['fvAEPg']['attributes']['name']] = int(i['fvAEPg']['children'][0]['fvRsNodeAtt']['attributes']['encap'][5:])
        return epg_vlan_mapping

    def _assign_vlan(self):
        '''
        Assign the first vlan id to a certain EPG from unused vlan pool.
        :return: dict of vlan id. if it is '0', no available vlan id on this leaf.
        '''
        url = '/api/mo/uni/tn-%s/ap-%s/epg-%s.json' %(self.tenant, self.anp, self.service)
        epg_vlan_mapping = self._get_epg_vlan()
        all_vlans = self.vlanpool
        if epg_vlan_mapping <> '0':
            for epg in epg_vlan_mapping:
                for i in all_vlans:
                    if epg_vlan_mapping[epg] == i:
                        all_vlans.remove(epg_vlan_mapping[epg])
        if not all_vlans:
            return {'vlan-id':'0', 'rest_result':'vlan pool is empty.'}
        vlan = str(all_vlans[0])
        body = {
                "fvRsNodeAtt":{
                  "attributes":{
                    "encap":"vlan-%s" %vlan,
                    "instrImedcy":"immediate",
                    "mode":"regular",
                    "tDn":"topology/%s/node-%s" %(self.pod, self._get_leaf()),
                    "status":"created"
                    }
                  }
               }
        result = _build_everything(url,body)
        return {'vlan-id':vlan, 'rest_result':result}

    def _get_all_epgs(self):
        '''
        For now, get all epgs in one pre-configured ANP of this tenant
        :return:
        '''
        url = '/api/node/mo/uni/tn-%s/ap-%s.json?query-target=children&target-subtree-class=fvAEPg' %(self.tenant, self.anp)
        result = _get_everything(url)
        if result['totalCount'] == '0':
            return result['totalCount']
        epg_list = []
        for epg in result['imdata']:
            epg_list.append(epg)
        return epg_list

    def _get_epg_leaf_vlan(self):
        '''
        Check if the epg is binded with certain leaf with a vlan, if yes, return vlan id
        :return: vlan id of this epg, '0' means no vlan id for this epg on the leaf or on the pod.
        '''
        url = '/api/node/mo/uni/tn-%s/ap-%s/epg-%s.json?query-target=children&target-subtree-class=fvRsNodeAtt' %(self.tenant, self.anp, self.service)
        result = _get_everything(url)
        if result['totalCount'] == '0':
            return result['totalCount']
        node_list = result['imdata']
        leaf = self._get_leaf()
        for node in node_list:
            node_id = node['fvRsNodeAtt']['attributes']['tDn'].split('/')[2]
            if leaf == node_id[5:]:
                return node['fvRsNodeAtt']['attributes']['encap'][5:]
        return '0'

    def _get_subnet(self):
        '''
        Check if a subnet is exists in the pod.
        :return: '1' for yes, '0' for no.
        '''
        url = '/api/class/fvSubnet.json?query-target=self&query-target-filter=eq(fvSubnet.ip,"%s")&rsp-prop-include=naming-only' %self.subnet
        result = _get_everything(url)
        if result['totalCount'] == '0':
            return result['totalCount']
        subnet_list = result['imdata']
        for subnet in subnet_list:
            if self.subnet == subnet['fvSubnet']['attributes']['ip']:
                return '1'
        return '0'

    def _add_subnet_bd(self):
        '''
        Add a subnet to an exist BD
        :return: rest result
        '''
        url = '/api/mo/uni/tn-%s/BD-%s.json' %(self.tenant, self.service)
        body = {
                "fvSubnet":{
                  "attributes":{
                    "ip": self.subnet,
                    "scope":"private",
                    "status":"created"
                    }
                  }
               }
        return _build_everything(url, body)

    def start_create(self):
        '''
        Logic of network behavior when docker is created.
        '''
        leaf = self._get_leaf()
        if leaf == '0':
            return logging.warning('No leaf found for host with IP %s' %self.hostip)
        epg = self._get_epg()
        if epg == '0':
            self._create_bd_subnet()
            self._create_epg()
            vlan_id = self._assign_vlan()['vlan-id']
            return vlan_id
        else:
            epg_leaf_vlan = self._get_epg_leaf_vlan()
            if epg_leaf_vlan == '0':
                vlan_id = self._assign_vlan()['vlan-id']
                return vlan_id
            else:
                is_subnet = self._get_subnet()
                if is_subnet == '0':
                    self._add_subnet_bd()
                return epg_leaf_vlan

def main():
    '''
    Initate APIC Token and conduct following operations.
    :return:
    '''
    global APIC_TOKEN
    a=mini_rest.rest_it()
    a.init_conf(conf=[
        CONTROLLER,
        '/api/aaaLogin.json',
        'POST',
        {"aaaUser" : {"attributes" : {"name" : "admin","pwd" : "admin@123"}}},
        CERT
    ])
    result = a.rest_run()
    APIC_TOKEN = result['imdata'][0]['aaaLogin']['attributes']['token']

    dh = DockerHandler(preconf='./conf/preconf.json',input='./conf/input.json')
    dh.start_create()


if __name__ == "__main__":
    main()
