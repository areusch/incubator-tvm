# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

"""Defines a top-level glue class that operates the Transport and Flasher classes."""

import logging
import time

from .._ffi import get_global_func
from ..contrib import graph_runtime
from .base import _rpc_connect
from ..rpc import RPCSession
from .transport import TransportLogger


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

    def __init__(
        self, binary=None, flasher=None, transport_context_manager=None, session_name="micro-rpc"
    ):
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
        self._graph_runtime = None

    def get_system_lib(self):
        return self._rpc.get_function("runtime.SystemLib")()

    def __enter__(self):
        """Initialize this session and establish an RPC session with the on-device RPC server.

        Returns
        -------
        Session :
            Returns self.
        """
        if self.flasher is not None:
            self.transport_context_manager = self.flasher.flash(self.binary)
            time.sleep(3.0)

        self.transport = TransportLogger(
            self.session_name, self.transport_context_manager, level=logging.INFO
        ).__enter__()
        self._rpc = RPCSession(
            _rpc_connect(self.session_name, self.transport.write, self.transport.read)
        )
        self.context = self._rpc.cpu(0)
        return self

    def __exit__(self, exc_type, exc_value, exc_traceback):
        """Tear down this session and associated RPC session resources."""
        self.transport.__exit__(exc_type, exc_value, exc_traceback)


def create_local_graph_runtime(graph_json_str, mod, ctx):
    """Create a local graph runtime driving execution on the remote CPU context given.

    Parameters
    ----------
    graph_json_str : str
        A string containing the graph representation.

    mod : tvm.runtime.Module
        The remote module containing functions in graph_json_str.

    ctx : tvm.Context
        The remote CPU execution context.

    Returns
    -------
    tvm.contrib.GraphRuntime :
         A local graph runtime instance that executes on the remote device.
    """
    device_type_id = [ctx.device_type, ctx.device_id]
    fcreate = get_global_func("tvm.graph_runtime.create")
    return graph_runtime.GraphModule(fcreate(graph_json_str, mod, *device_type_id))
