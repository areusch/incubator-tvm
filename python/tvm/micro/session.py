import os
import tempfile
import time

from .base import _rpc_connect
from ..rpc import RPCSession
from .transport import TransportLogger
from . import compiler
from . import micro_binary
from .. import register_func, register_object


class Session:
    """MicroTVM Device Session

    Parameters
    ----------
    config : dict
        configuration for this session (as generated by
        `tvm.micro.device.host.default_config()`, for example)

    Example
    --------
    .. code-block:: python

      c_mod = ...  # some module generated with "c" as the target
      dev_config = micro.device.arm.stm32f746xx.default_config('127.0.0.1', 6666)
      with tvm.micro.Session(dev_config) as sess:
          micro_mod = sess.create_micro_mod(c_mod)
    """

    def __init__(self, binary=None, flasher=None, transport_context_manager=None,
                 session_name='micro-rpc'):
        """Configure a new session.

        Parameters
        ----------
        binary : MicroBinary
            If given, `flasher` must also be given. During session initialization, this binary will
            be flashed to the device before the transport is created.
        flasher : Flasher
            If given, `binary` must also be given. Used to flash `binary` during session
            initialization.
        transport_context_manager : ContextManager[transport.Transport]
            If given, `flasher` and `binary` should not be given. On entry, this context manager
            should establish a tarnsport between this TVM instance and the device.
        session_name : str
            Name of the session, used for debugging.
        """
        self.binary = binary
        self.flasher = flasher
        self.transport_context_manager = transport_context_manager
        self.session_name = session_name

        self._rpc = None

    def get_system_lib(self):
        return self._rpc.get_function('runtime.SystemLib')()

    def __enter__(self):
        """Initialize this session and establish an RPC session with the on-device RPC server.

        Returns
        -------
        Session :
            Returns self.
        """
        if self.flasher is not None:
            self.transport_context_manager = self.flasher.Flash(self.binary)
            time.sleep(3.0)

        import logging
        self.transport = TransportLogger(
            self.session_name, self.transport_context_manager, level=logging.INFO).__enter__()
        self._rpc = RPCSession(_rpc_connect(
            self.session_name, self.transport.write, self.transport.read))
        self.context = self._rpc.cpu(0)
        return self

    def __exit__(self, exc_type, exc_value, exc_traceback):
        """Tear down this session and associated RPC session resources."""
        self.transport.__exit__(exc_type, exc_value, exc_traceback)


RPC_SESSION_CONFIG = None


def load_rpc_session_config(file_name):
    global RPC_SESSION_CONFIG
    with open(file_name) as json_f:
        RPC_SESSION_CONFIG = json.load(json_f)


RPC_SESSION = None


@register_func("tvm.micro.create_micro_session")
def create_micro_session(build_result_filename, build_result_bin, flasher_factory_json):
    global RPC_SESSION
    if RPC_SESSION is not None:
        raise Exception('Micro session already established')

    with tempfile.NamedTemporaryFile(prefix=build_result_filename, mode='w+b') as tf:
        tf.write(build_result_bin)
        tf.flush()

#    if RPC_SESSION_CONFIG is None:
#        raise Exception('No RPC_SESSION_CONFIG loaded')

        binary = micro_binary.MicroBinary.unarchive(
            tf.name, os.path.join(tempfile.mkdtemp(), 'binary'))
        flasher_obj = compiler.FlasherFactory.from_json(flasher_factory_json).instantiate()

        RPC_SESSION = Session(binary=binary, flasher=flasher_obj)
        RPC_SESSION.__enter__()
        return RPC_SESSION._rpc._sess


@register_func
def destroy_micro_session():
    global RPC_SESSION
    if RPC_SESSION is not None:
        exc_type, exc_value, traceback = RPC_SESSION.__exit__(None, None, None)
        RPC_SESSION = None
        if (exc_type, exc_value, traceback) != (None, None, None):
            e = exc_type(exc_value)  # See PEP 3109
            e.__traceback__ = traceback
            raise e
