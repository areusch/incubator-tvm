import contextlib
import copy
import glob
import os

import numpy

import tvm
import tvm.micro

from tvm.micro.contrib import mbed
from tvm.contrib import device_util
from tvm.contrib import util

BUILD = True
DEBUG = False

def test_compile_runtime():
  """Test compiling the on-device runtime."""
  target = tvm.target.target.micro('stm32f746xx')

  A = tvm.te.placeholder((2,), dtype='int8')
  B = tvm.te.placeholder((1,), dtype='int8')
  C = tvm.te.compute(A.shape, lambda i: A[i] + B[0], name='C')

  s = tvm.te.create_schedule(C.op)

  with tvm.transform.PassContext(opt_level=3, config={'tir.disable_vectorize': True}):
    mod = tvm.build(s, [A, B, C], target, target_host=target, name='add')

  workspace = tvm.micro.Workspace(debug=True)

  rpc_session = tvm.rpc.connect('127.0.0.1', 9090)
  project_dir = '/Users/andrew/ws/stm-nucleo/test'
  compiler = mbed.MbedCompiler(
    project_dir=project_dir,
    mbed_target='NUCLEO_F746ZG',
    mbed_toolchain='GCC_ARM',
    debug_rpc_session=rpc_session,
  )

  root_dir = os.path.realpath(os.path.join(os.path.dirname(__file__), '..', '..'))
  opts = tvm.micro.DefaultOptions(f'{project_dir}/crt')
  # TODO(weberlo) verify this is necessary
  opts['bin_opts']['ccflags'] = ['-std=gnu++14']
  opts['lib_opts']['ccflags'] = ['-std=gnu++14']

  if BUILD:
    micro_binary = tvm.micro.build_static_runtime(workspace, compiler, mod, **opts)
  lib_opts['cflags'].pop()

  device_transport = device_util.DeviceTransportLauncher({
    'num_instances': 1,
    'use_tracker': False,
  })

  with contextlib.ExitStack() as exit_stack:
    flasher_kw = {
      'debug': DEBUG,
      'debug_remote_hostport': '{}:{:d}'.format(*device_transport.openocd_gdb_host_port_tuple(0)),
    }

    if DEBUG:
      flasher_kw['debug_wrapping_context_manager'] = device_transport.launch(None)

    flasher = compiler.Flasher(**flasher_kw)
    if BUILD:
      sess = exit_stack.enter_context(tvm.micro.Session(binary=micro_binary, flasher=flasher))
    else:
      sess = exit_stack.enter_context(tvm.micro.Session(transport_context_manager=flasher.Transport(
        micro_binary=tvm.micro.MicroBinary('/private/var/folders/9y/3j808g591ln3kys4qpyl3qmc0000gn/T/tvm-debug-mode-tempdirs/2020-06-16T17-42-32___fa8goneh/00000/build/runtime', 'runtime.bin', debug_files=['runtime.elf']))))
    A_data = tvm.nd.array(numpy.array([2, 3], dtype='int8'), ctx=sess.context)
    assert (A_data.asnumpy() == numpy.array([2, 3])).all()

    B_data = tvm.nd.array(numpy.array([4], dtype='int8'), ctx=sess.context)
    assert (B_data.asnumpy() == numpy.array([4])).all()

    C_data = tvm.nd.array(numpy.array([0, 0], dtype='int8'), ctx=sess.context)
    assert (C_data.asnumpy() == numpy.array([0, 0])).all()

    print('get system lib')
    system_lib = sess._rpc.system_lib()
#    system_lib_func = sess._rpc.get_function('get_system_lib')
#    system_lib = system_lib_func()
    print('got system lib', system_lib)
    system_lib.get_function('add')(A_data, B_data, C_data)
    print('got data!', C_data.asnumpy())
    assert (C_data.asnumpy() == numpy.array([6, 7])).all()


if __name__ == '__main__':
  test_compile_runtime()