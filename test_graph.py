import tempfile

import os
import numpy as np
import tvm
import tvm.micro
import tvm.relay
import tvm.relay.backend
import tvm.relay.testing

#/    %0 = cast(cast(%data, int16) - cast(%mean_data, int16), int8);
 # %2 = nn.bias_add(%1, cast(%conv0_bias, "int32"), axis=3);

RELAY_MODEL = """
#[version = "0.0.5"]
def @main(%data : Tensor[(1, 3, 64, 64), uint8], %weight : Tensor[(8, 3, 5, 5), int8]) {
    %1 = nn.conv2d(
         %data,
         %weight,
         padding=[2, 2],
         channels=8,
         kernel_size=[5, 5],
         data_layout="NCHW",
         kernel_layout="OIHW",
         out_dtype="int32");
  %3 = right_shift(%1, 9);
  %4 = cast(%3, dtype="int8");
  %4
}
"""


def test_relay_model():
  model = tvm.IRModule()
  mod = tvm.parser.fromtext(RELAY_MODEL)
#  mod, params = tvm.relay.testing.create_workload(func)main
  main_func = mod['main']
  shape_dict = {p.name_hint: p.checked_type.concrete_shape for p in main_func.params}
  weight_data = np.random.random_integers(-127, 128, shape_dict['weight']).astype("int8")
  params = {'weight': weight_data}

#  print(str(mod))
  target = 'c -mcpu=native --runtime=c --system-lib'
  with tvm.transform.PassContext(opt_level=3, config={"tir.disable_vectorize": True}):
    lib = tvm.relay.backend.compile_engine.get().lower(mod['main'], target)
    print('lib', lib.funcs)
    print('optimize', tvm.relay.optimize(mod, target, params=params)[0].astext(show_meta_data=False))
    lib, aot = tvm.relay.build(mod, target, params=params)
    print('csource', lib.lib.get_source())

  ws = tvm.micro.Workspace(debug=True)
  mod_path = f'{ws.path}/lib.c'
  lib.lib.save(mod_path, 'cc')
  with open(mod_path, 'a+') as f:
    f.write(aot)

  print('------------------- Graph -------------------')
  print(lib.graph_json)
  print('-------------------- AOT --------------------')
  print(aot)

  compiler = tvm.micro.DefaultCompiler(target)
  opts = tvm.micro.default_options(os.path.join(tvm.micro.CRT_ROOT_DIR, "host"))
  micro_bin = tvm.micro.build_static_runtime(ws, compiler, mod_path, opts['lib_opts'], opts['bin_opts'])
  with tvm.micro.Session(binary=micro_bin, flasher=compiler.flasher(debug=False)) as sess:
    mod = sess.get_system_lib()
    main = mod.get_function('main_func')
    A_data = np.random.random_integers(0, 255, [1, 64, 64, 3]).astype("uint8")
    A = tvm.nd.array(A_data, ctx=sess.context)
    B = tvm.nd.array(np.zeros([1, 8, 64, 64], dtype="int8"), ctx=sess.context)
    main(A, B)
    aot_output = B.asnumpy()


  with tvm.micro.Session(binary=micro_bin, flasher=compiler.flasher(debug=False)) as sess:
    graph_mod = tvm.micro.create_local_graph_runtime(
      lib.get_json(), sess.get_system_lib(), sess.context
    )

    graph_mod.set_input(**lib.params)
    graph_mod.run(data=tvm.nd.array(A_data, ctx=sess.context))
    graph_output = graph_mod.get_output(0).asnumpy()

  np.testing.assert_allclose(aot_output, graph_output)
  print("all passed")


test_relay_model()
