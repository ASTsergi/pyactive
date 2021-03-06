"""
Author: Edgar Zamora Gomez  <edgar.zamora@urv.cat>
"""

from pyactive.constants import METHOD, MODE, SRC, TO, FROM, TARGET, TYPE, RESULT, PARAMS, RPC_ID, SYNC, CALL, ERROR, MULTI, TCP
from urlparse import urlparse
import stackless
import copy
from pyactive.exception import AtomError, TimeoutError, NotFoundDispatcher, MethodError
from pyactive.util import Ref, ref_l, ref_d
from collections import deque
from taskletDelay import sleep
from tcp_server import Server

pending = {} 
tasklets = {}

class Pyactive(Ref):
    
    def __init__(self):
        self.channel = stackless.channel()
        self.activate = stackless.channel()
        self.aref = ''
        self.ref = False
        self.group = None
        self.queue = deque([])
        self.channel.preference = -1
        self.running = False
        
        
    
    def run(self):
        self.running = True
        self.in_task = stackless.tasklet(self.enqueue)()
        self.msg_task = stackless.tasklet(self.processMessage)()
        tasklets[self.msg_task] = self.aref
        
    def registry_object(self, obj):
        self.obj = obj
        
    #@async
    def stop(self):
        self.running = False
      
    def __eq__(self, other):
        return self.get_aref() == other.get_aref()
    
    def set_aref(self, aref):
        self.aref = aref
        aurl = urlparse(aref)
        module, kclass, oid = aurl.path[1:].split('/')
        self._id = oid
    
        
    def enqueue(self):
        while True:
            msg = self.channel.receive()
            self.queue.append(msg)
            self.receive_all()
        
        
    def receive_all(self):    
        while self.channel.balance > 0:
            msg = self.channel.receive()
            self.queue.append(msg)
        if self.running:
            self.activate.send(None)
             
    def processMessage(self):
        while self.running:
            while len(self.queue) > 0:
                self.receive(self.queue.popleft())
            self.activate.receive()
    
    def send(self, msg):       
        msg[SRC] = self.channel
        msg[TO] = self.aref
        msg[TARGET] = self.target
        msg[TYPE] = CALL
        if msg[MODE] == SYNC:
            pending[msg[RPC_ID]] = 1
            
        self.out.send(msg)
        
    def init_parallel(self):
        '''Put parallel wrapper on object methods that need'''
        for name in self.parallelList:
            setattr(self.obj, name, ParallelWraper(getattr(self.obj, name), self.aref))

    
    def send2(self, target, msg):
        target.send(msg)
      
    def receive_result(self):
        '''recive result of synchronous calls'''
        msg = self.channel.receive()
        return msg[RESULT] 
       
    def receive(self, msg):
        ''' receive messages and invokes object method'''
        
        invoke = getattr(self.obj, msg[METHOD])
        params = msg[PARAMS]
        result = None
        try:
            result = invoke(*params)
        except AtomError, e:
            result = AtomError(e)
            msg[ERROR] = 1
        except TypeError, e2:
            result = MethodError()
            msg[ERROR]=1
            
        if result != None and msg[MODE] == SYNC:
            msg2 = copy.copy(msg)
            target = msg2[SRC]
            msg2[TYPE] = RESULT
            msg2[RESULT] = result
            del msg2[PARAMS]
            del msg2[SRC]
            if pending.has_key(msg[RPC_ID]):
                del pending[msg[RPC_ID]]
                _from = msg2[FROM]
                msg2[FROM] = self.aref
                msg2[TO] = _from
                self.send2(target, msg2)
                
    def get_proxy(self):
            
        return self.host.load_client(self.channel, self.aref, get_current())
    
    def ref_on(self):
        self.ref = True
        self.receive = ref_l(self.receive)
        self.send2 = ref_d(self.send2)
        
    #@sync(2)           
    def ping(self):
        return True
    
    def get_aref(self):
        return self.aref   
    
    def get_id(self):
        return self._id 
    
    def get_gref(self):
        if self.group != None:
            return self.group.aref    
        
        
class ParallelWraper():
    def __init__(self, send, aref):
        self.__send= send
        self.__aref = aref
        
    def __call__(self, *args, **kwargs):
        t = stackless.tasklet(self.__send)(*args)
        tasklets[t] = self.__aref

class TCPDispatcher(Pyactive):
   
    def __init__(self, host, addr):
        Pyactive.__init__(self)  
        ip, port = addr
        self.name = ip + ':' + str(port)
        self.conn = Server(ip, port, self)
        self.addr = addr
        self.host = host
        
        self.callback = {}

    #@async
    def _stop(self):
        self.conn.close()
        super(TCPDispatcher, self)._stop()
     
            
    def receive(self, msg):
        if msg[MODE] == SYNC and msg[TYPE] == CALL:
            self.callback[msg[RPC_ID]] = msg[SRC]
        msg[SRC] = self.addr
        
        self.conn.send(msg)
   
    def is_local(self, name):
        return name == self.name
    
    def on_message(self, msg):
        try:
            if msg[TYPE] == RESULT:
                if msg.has_key(MULTI):
                    target = self.callback[msg[RPC_ID]]
                    target.send(msg)
                if pending.has_key(msg[RPC_ID]):
                    del pending[msg[RPC_ID]]
                    target = self.callback[msg[RPC_ID]]
                    del self.callback[msg[RPC_ID]]
                    target.send(msg)
            else:
                if msg[MODE] == SYNC:
                    msg[TARGET] = msg[SRC]
                    msg[SRC] = self.channel
                    pending[msg[RPC_ID]] = 1
                aref = msg[TO]
                aurl = urlparse(aref)
                self.host.objects[aurl.path].channel.send(msg)
        except Exception, e:
            print e, 'TCP ERROR2'
    
def new_TCPdispatcher(host, dir):
    tcp = TCPDispatcher(host, dir) 
    tcp.run()
    return tcp

def new_dispatcher(host, transport):
    '''Select and create new dispatcher '''
    dispatcher_type = transport[0]
    if dispatcher_type == TCP:
        return new_TCPdispatcher(host, transport[1])
    else:
        raise NotFoundDispatcher()

def get_current():
    current = stackless.getcurrent()
    if tasklets.has_key(current):
        return tasklets[current]    

def send_timeout(channel, rpc_id):
    if pending.has_key(rpc_id):
        del pending[rpc_id]
        msg = {}
        msg[TYPE] = ERROR
        msg[RESULT] = TimeoutError()
        channel.send(msg)
    

def launch(func, params=[]):
    t1 = stackless.tasklet(func)(*params)
    tasklets[t1] = 'atom://localhost/' + func.__module__ + '/' + func.__name__

    while t1.scheduled:
        stackless.schedule()
        sleep(0.01)  
       
def serve_forever(func, params=[]):
    t1 = stackless.tasklet(func)(*params)
    tasklets[t1] = 'atom://localhost/' + func.__module__ + '/' + func.__name__
    while True:
        stackless.run()
        sleep(0.01)