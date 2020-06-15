/*
 * Licensed to the Apache Software Foundation (ASF) under one
 * or more contributor license agreements.  See the NOTICE file
 * distributed with this work for additional information
 * regarding copyright ownership.  The ASF licenses this file
 * to you under the Apache License, Version 2.0 (the
 * "License"); you may not use this file except in compliance
 * with the License.  You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing,
 * software distributed under the License is distributed on an
 * "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
 * KIND, either express or implied.  See the License for the
 * specific language governing permissions and limitations
 * under the License.
 */

/*!
 * \file tvm/runtime/packed_func.c
 * \brief PackedFunc implementation.
 */

#include <string.h>

#include "packed_func.h"
#include "logging.h"

DLDataType String2DLDataType(const char* s) {
  DLDataType t;
  // handle None type
  if (strlen(s) == 0) {
    t.bits = 0;
    t.lanes = 0;
    t.code = kTVMOpaqueHandle;
    return t;
  }
  t.bits = 32;
  t.lanes = 1;
  const char* scan;
  if (!strncmp(s, "int", 3)) {
    t.code = kDLInt;
    scan = s + 3;
  } else if (!strncmp(s, "uint", 4)) {
    t.code = kDLUInt;
    scan = s + 4;
  } else if (!strncmp(s, "float", 5)) {
    t.code = kDLFloat;
    scan = s + 5;
  } else if (!strncmp(s, "handle", 6)) {
    t.code = kTVMOpaqueHandle;
    t.bits = 64;  // handle uses 64 bit by default.
    scan = s + 6;
  } else if (!strcmp(s, "bool")) {
    t.code = kDLUInt;
    t.bits = 1;
    t.lanes = 1;
    return t;
  } else {
    scan = s;
    fprintf(stderr, "unknown type %s\n", s);
  }
  char* xdelim;
  uint8_t bits = (uint8_t)(strtoul(scan, &xdelim, 10));
  if (bits != 0) t.bits = bits;
  char* endpt = xdelim;
  if (*xdelim == 'x') {
    t.lanes = (uint16_t)(strtoul(xdelim + 1, &endpt, 10));
  }
  if (!(endpt == s + strlen(s))) {
    fprintf(stderr, "unknown type %s\n", s);
  }
  return t;
}

TVMArgs TVMArgs_Create(TVMValue* values, uint32_t* tcodes, uint32_t values_count) {
  uint32_t idx;
  TVMArgs args;
  memset(&args, 0, sizeof(args));
  for (idx = 0; idx < values_count; idx++) {
    memcpy(args.values + idx, values + idx, sizeof(TVMValue));
    args.tcodes[idx] = tcodes[idx];
  }
  args.values_count = values_count;
  return args;
}

int TVMNoOperation(TVMValue* args, int* type_codes, int num_args,
                   TVMRetValueHandle ret, void* res) {
  return 0;
}

void TVMPackedFunc_Call(TVMPackedFunc* pf) {
  pf->fexec(pf->args.values, pf->args.tcodes, pf->args.values_count, 0, 0);
}

void TVMPackedFunc_SetArgs(TVMPackedFunc* pf, const TVMArgs* args) {
  memcpy(&(pf->args), args, sizeof(TVMArgs));
}

/*!
 * \brief strcmp against the next string in the registry, and return the end.
 *
 * Regardless of return value, after calling this function, cursor's value will be modified to
 * point at the \0 at the end of the string it currently points to.
 *
 * \param cursor Pointer to cursor to first string to compare.
 * \param name Pointer to reference string.
 * \return 0 if the string pointed to by cursor == name; non-zero otherwise.
 */
static int strcmp_cursor(const char** cursor, const char* name) {
  int return_value = 0;
  while (**cursor != 0) {
    char c = **cursor;
    char n = *name;
    return_value = ((int) n) - ((int) c);
    if (n == 0 || c == 0) {
      break;
    }

    name++;
    (*cursor)++;
  }

  while (**cursor != 0) {
    (*cursor)++;
  }

  return return_value;
}


int TVMFuncRegistry_Lookup(const TVMFuncRegistry* reg, const char* name, tvm_function_index_t* function_index) {
  tvm_function_index_t idx;
  const char* reg_name_ptr;

  idx = 0;
  for (reg_name_ptr = reg->names + 1; *reg_name_ptr; reg_name_ptr++) {
    if (!strcmp_cursor(&reg_name_ptr, name)) {
      *function_index = idx;
      return 0;
    }

    idx++;
  }

  return -1;
}

int TVMFuncRegistry_GetByIndex(const TVMFuncRegistry* reg,  tvm_function_index_t function_index, TVMBackendPackedCFunc* out_func) {
  uint8_t num_funcs;

  num_funcs = reg->names[0];
  if (function_index >= num_funcs) {
    return -1;
  }

  *out_func = reg->funcs[function_index];
  return 0;
}

void TVMMutableFuncRegistry_Create(TVMMutableFuncRegistry* reg, uint8_t* buffer, size_t buffer_size_bytes) {
  memset(reg, 0, sizeof(*reg));
  reg->registry.names = (const char*) buffer;
  buffer[0] = 0;  // number of functions present in buffer.
  buffer[1] = 0;  // end of names list marker.

  // compute a guess of the average size of one entry:
  //  - assume average function name is around ~10 bytes
  //  - 1 byte for \0
  //  - size of 1 function pointer
  size_t one_entry_size_bytes = 10 + 1 + sizeof(void*);
  reg->max_functions = buffer_size_bytes / one_entry_size_bytes;
  reg->registry.funcs = (TVMBackendPackedCFunc*) (buffer + buffer_size_bytes - reg->max_functions * sizeof(void*));
}

int TVMMutableFuncRegistry_Set(TVMMutableFuncRegistry* reg, const char* name, TVMBackendPackedCFunc func,
                               int override) {
  size_t idx;
  char* reg_name_ptr;

  idx = 0;
  // note: safe to discard const qualifier here, since reg->registry.names was set from
  // TVMMutableFuncRegistry_Create above.
  for (reg_name_ptr = (char*) reg->registry.names + 1; *reg_name_ptr != 0; reg_name_ptr++) {
    if (!strcmp_cursor((const char**) &reg_name_ptr, name)) {
      if (override == 0) {
        return -1;
      }
      reg->registry.funcs[idx] = func;
      return 0;
    }

    idx++;
  }

  size_t name_len = strlen(name);
  if (idx > reg->max_functions || (reg_name_ptr + name_len) > ((const char*) reg->registry.funcs)) {
    return -1;
  }

  strcpy(reg_name_ptr, name);
  reg_name_ptr += name_len + 1;
  *reg_name_ptr = 0;
  reg->registry.funcs[idx] = func;
  ((char*) reg->registry.names)[0]++;  // increment num_funcs.

  return 0;
}