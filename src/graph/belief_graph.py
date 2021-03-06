""" A neural chatbot using sequence to sequence model with
attentional decoder.

This is based on Google Translate Tensorflow model
https://github.com/tensorflow/models/blob/master/tutorials/rnn/translate/

Sequence to sequence model by Cho et al.(2014)

Created by Chip Huyen as the starter code for assignment 3,
class CS 20SI: "TensorFlow for Deep Learning Research"
cs20si.stanford.edu

This file contains the code to run the model.

See readme.md for instruction on how to run the starter code.

This implementation learns NUMBER SORTING via seq2seq. Number range: 0,1,2,3,4,5,EOS

https://papers.nips.cc/paper/5346-sequence-to-sequence-learning-with-neural-networks.pdf

See README.md to learn what this code has done!

Also SEE https://stackoverflow.com/questions/38241410/tensorflow-remember-lstm-state-for-next-batch-stateful-lstm
for special treatment for this code

Belief Graph
"""
import pickle
import os
import sys
import uuid
import json

parentdir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, parentdir)

from graph.node import Node


class Graph(Node, object):
    ROOT = "ROOT".lower()

    def __init__(self, slot, value, fields, node_type, id):
        super(Graph, self).__init__(slot=slot, value=value,
                                    fields=fields, node_type=node_type, id=id)
        # This parameter stores all
        # value is a list of nodes that share the same.
        self.node_header = dict()
        self.id_node = dict()
        """
        key as slot, value as type
        """
        self.slots = dict()
        self.slots_trans = dict()
        self.range_adapter_mapper = dict()
        self._prebuild_range_adaper()
        """
        price:price
        tv.size, phone.size, pc.size: __inch__
        tv.distance: __meter__
        ac.power: ac.power
        """

    def _prebuild_range_adaper(self):
        self.range_adapter_mapper['price'] = 'price'
        self.range_adapter_mapper['tv.size'] = '__inch__'
        self.range_adapter_mapper['phone.size'] = '__inch__'
        self.range_adapter_mapper['pc.size'] = '__inch__'
        self.range_adapter_mapper['tv.distance'] = '__meter__'
        self.range_adapter_mapper['ac.power_float'] = 'ac.power'
        self.range_adapter_mapper['fr.height'] = 'height'
        self.range_adapter_mapper['fr.width'] = 'width'
        self.range_adapter_mapper['phone.rmem'] = 'memory'
        self.range_adapter_mapper['pc.mem'] = 'memory'
        self.range_adapter_mapper['fr.vol'] = '__L__'

    def range_adapter(self, key):
        if key not in self.range_adapter_mapper:
            return ''
        return self.range_adapter_mapper[key]

    def is_entity_value(self, value):
        if len(self.node_header[value]) == 1:
            return False
        nodes = self.node_header[value]
        slots = [node.slot for node in nodes]
        if len(set(slots)) == 1:
            return False
        else:
            return True

    def get_node_connected_slots(self, value):
        """
        苹果.slot 可以是品牌,也可以是水果
        """
        nodes = self.node_header[value]
        slots = set()
        for node in nodes:
            slots.add(node.slot)
        return list(slots)

    def get_root_node(self):
        return self.node_header[self.ROOT][0]

    def get_field_type(self, field):
        if field not in self.slots:
            return None
        return self.slots[field]

    def get_nodes_by_slot(self, slot):
        field_nodes = []
        for key, nodes in self.node_header.items():
            for node in nodes:
                if node.slot == slot:
                    field_nodes.append(node)
        return field_nodes

    def get_nodes_by_value(self, node_value):
        """
        get_nodes_by_value("苹果")...
        return a listraw
        """
        if node_value not in self.node_header:
            return []
        return self.node_header[node_value]

    def get_nodes_by_value_and_field(self, value, field):
        nodes = self.get_nodes_by_value(value)
        filtered = []
        for node in nodes:
            if field in node.ins:
                filtered.append(node)
        return filtered

    def has_node_by_value(self, node_value):
        return node_value in self.node_header

    def get_node_by_id(self, id):
        return self.id_node[id]

    def has_slot(self, slot):
        return slot in self.slots


def load_belief_graph(path, output_model_path):
    belief_graph = None
    node_header = {}
    id_node = {}
    slots = []
    with open(path, 'r') as f:
        for line in f:
            if line.startswith("-"):
                line = line.strip("\n").replace(" ", "").replace("\t", "")
                print(line.strip("\n"))
                value, id, slot, fields_, node_type = line.split("#")
                fields = dict()
                value = value.replace("-", "")

                if fields_:
                    for fi in fields_.split(","):
                        field = fi.split(":")[0]
                        prob = float(fi.split(":")[1])
                        fields[field] = prob
                if value == "ROOT".lower():
                    node = Graph(
                        value=value, fields=fields, slot=slot, id=id, node_type=node_type)
                    if not belief_graph:
                        belief_graph = node
                else:
                    node = Node(value=value, fields=fields,
                                slot=slot, id=id, node_type=node_type)
                if value not in node_header:
                    node_header[value] = []
                node_header[value].append(node)
                if id in id_node:
                    raise ValueError("id")
                id_node[id] = node

    belief_graph.node_header = node_header
    belief_graph.id_node = id_node

    with open(path, 'r') as f:
        for line in f:
            if line.startswith("+"):
                line = line.strip("\n").replace(" ", "").replace("\t", "")
                print(line.strip("\n"))
                parent_, slot, value_type, children_ = line.split('#')
                parent_ = parent_.replace("+", "")
                parent, parent_id = parent_.split("/")
                node = id_node[parent_id]
                if parent != node.value:
                    raise ValueError("id")

                for c in children_.split(","):
                    splitted = c.split("/")
                    if value_type is not "KEY":
                        node.set_field_type(slot, value_type)

                    if len(splitted) == 2:
                        value = splitted[0]
                        id = splitted[1]
                        child_node = id_node[id]
                        if value != child_node.value:
                            raise ValueError("id")
                        node.add_node(child_node)
                    else:
                        value = splitted[0]
                        # property node
                        id = str(uuid.uuid4())
                        child_node = Node(value=value, fields=dict(),
                                          slot=slot, id=id, node_type="property")
                        if value not in node_header:
                            node_header[value] = []
                        node_header[value].append(child_node)
                        node.add_node(child_node)
    with open(output_model_path, "wb") as omp:
        pickle.dump(belief_graph, omp)


def load_belief_graph_from_tables(files, output_file):
    belief_graph = None
    node_header = {}
    id_node = {}
    slots = dict()
    main_nodes = dict()
    # stage 1, build node
    for f in files:
        with open(f, 'r', encoding='utf-8') as inpt:
            for line in inpt:
                line = line.strip('\n').replace(' ', '').lower()
                note, cn, slot, node_type, slot_value = line.split('|')
                if note == '-':
                    slots[slot] = node_type
                    _id = str(uuid.uuid4())
                    # node = Node(value=slot_value, fields=dict(),
                    #                   slot=slot, id=_id, node_type="property")
                    if slot_value == "ROOT".lower():
                        node = Graph(
                            value=slot_value, fields=dict(), slot=slot, id=id, node_type=slot)
                        if not belief_graph:
                            belief_graph = node
                    else:
                        node = Node(value=slot_value, fields=dict(),
                                    slot=slot, id=id, node_type=slot)
                        main_nodes[slot_value] = node
                    if slot_value not in node_header:
                        node_header[slot_value] = []
                    else:
                        raise ValueError(
                            'non property node value should be unique')
                    node_header[slot_value].append(node)
                    id_node[_id] = node
                if note == '*':
                    if slot_value:
                        tokens = slot_value.split(",")
                        for t in tokens:
                            a, b = t.split(":")
                            node.fields[a] = float(b)
                    # node.fields = dict()
    belief_graph.id_node = id_node
    belief_graph.node_header = node_header
    belief_graph.slots = slots
    for f in files:
        with open(f, 'r', encoding='utf-8') as inpt:
            for line in inpt:
                line = line.strip('\n').replace(' ', '').lower()
                note, cn, slot, node_type, slot_value = line.split('|')
                if note == '-':
                    nodes = node_header[slot_value]
                    # print(slot_value, len(nodes))
                    if len(nodes) > 1 or len(nodes) == 0:
                        raise ValueError(
                            'non property node value should be unique')
                    else:
                        node = nodes[0]
                if note == '+':
                    slots[slot] = node_type
                    note, cn, slot, value_type, slot_value = line.split('|')
                    node.set_node_slot_trans(slot, cn)
                    belief_graph.slots_trans[slot] = cn
                    if value_type != Node.KEY:
                        # print(slot)
                        node.set_field_type(slot, value_type)
                        continue
                    names = slot_value.split(',')
                    for name in names:
                        if not name:
                            continue
                        if 'category' in slot:
                            try:
                                nodes = node_header[name]
                            except:
                                raise ValueError(line)
                            if len(nodes) > 1 or len(nodes) == 0:
                                raise ValueError(
                                    'non property node value should be unique')
                            else:
                                child_node = nodes[0]
                            node.add_node(child_node)
                            continue
                        _id = str(uuid.uuid4())
                        if name in main_nodes:
                            child_node = main_nodes[name]
                        else:
                            child_node = Node(value=name, fields=dict(),
                                              slot=slot, id=_id, node_type="property")
                            if name not in node_header:
                                node_header[name] = []
                            node_header[name].append(child_node)
                            id_node[id] = child_node
                        node.add_node(child_node, edge=slot, ignore_field_conflict=True)

    # print(json.dumps(belief_graph))
    with open(output_file, "wb") as omp:
        pickle.dump(belief_graph, omp)

def get_sunning():
    table_files = ['../../data/gen_product/冰箱.txt',
                   '../../data/gen_product/电视.txt',
                   '../../data/gen_product/digitals.txt',
                   '../../data/gen_product/homewares.txt',
                   '../../data/gen_product/空调.txt',
                   '../../data/gen_product/root.txt',
                   '../../data/gen_product/手机.txt',
                   '../../data/gen_product/电脑.txt',
                   '../../data/gen_product/grocery.txt',
                   '../../data/gen_product/fruits.txt']
    additional = "净水器.txt,household.txt,kitchenwares.txt,\
    剃毛器.txt,\
    加湿器.txt,\
    取暖器.txt,\
    吸尘器.txt,\
    咖啡机.txt,\
    垃圾处理机.txt,\
    多用途锅.txt,\
    干衣机.txt,\
    微波炉.txt,\
    打蛋器.txt,\
    扫地机器人.txt,\
    挂烫机.txt,\
    按摩器.txt,\
    按摩椅.txt,\
    排气扇.txt,\
    搅拌机.txt,\
    料理机.txt,\
    榨汁机.txt,\
    油烟机.txt,\
    洗碗机.txt,\
    洗衣机.txt,\
    浴霸.txt,\
    消毒柜.txt,\
    烟灶套装.txt,\
    烤箱.txt,\
    热水器.txt,\
    煮蛋器.txt,\
    燃气灶.txt,\
    电动剃须刀.txt,\
    电动牙刷.txt,\
    电压力锅.txt,\
    电吹风.txt,\
    电子秤.txt,\
    电水壶.txt,\
    电炖锅.txt,\
    电磁炉.txt,\
    电蒸炉.txt,\
    电风扇.txt,\
    电饭煲.txt,\
    电饼铛.txt,\
    相机.txt,\
    空气净化器.txt,\
    空调扇.txt,\
    美发器.txt,\
    美容器.txt,\
    豆浆机.txt,\
    足浴盆.txt,\
    酸奶机.txt,\
    采暖炉.txt,\
    除湿机.txt,\
    集成灶.txt,\
    面包机.txt,\
    饮水机.txt".split(',')
    additional = ['../../data/gen_product/' + a for a in additional]
    return table_files, additional

def get_bookstore():
    table_files = ['../../data/bookstore/root.txt',
                   '../../data/bookstore/书店注册.txt',
                   '../../data/bookstore/扫码.txt',
                   '../../data/bookstore/扫码关注成功.txt',
                   '../../data/bookstore/扫码关注失败.txt',
                   '../../data/bookstore/点击验证成功.txt',
                   '../../data/bookstore/点击验证失败.txt',]
    additional = []
    additional = ['../../data/bookstore/' + a for a in additional]
    return table_files, additional


if __name__ == "__main__":
    # load_belief_graph(
    #     "/home/deep/solr/memory/memory_py/data/graph/belief_graph.txt",
    #     "/home/deep/solr/memory/memory_py/model/graph/belief_graph.pkl")
    table_files, additional = get_bookstore()
    table_files.extend(additional)
    output_file = "../../model/graph/belief_graph.pkl"
    load_belief_graph_from_tables(table_files, output_file)
    with open(output_file,'rb') as f:
        graph=pickle.load(f)
    words=list(graph.node_header.keys())
    s=set()
    with open('../../data/dict/ext1.dic','r') as f:
        for line in f:
            line=line.strip()
            s.add(line)
    for w in words:
        s.add(w)
    with open('../../data/dict/ext1.dic','w') as f:
        for w in s:
            f.write(w+'\n')