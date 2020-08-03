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
 * \file session.h
 * \brief RPC Session
 */

#ifndef TVM_RUNTIME_CRT_RPC_SERVER_SESSION_H_
#define TVM_RUNTIME_CRT_RPC_SERVER_SESSION_H_

#include <inttypes.h>
#include "buffer.h"
#include "framing.h"
#include "write_stream.h"

namespace tvm {
namespace runtime {

enum class MessageType : uint8_t {
  kStartSessionMessage = 0x00,
  kLogMessage = 0x01,
  kNormalTraffic = 0x10,
};

typedef struct SessionHeader {
  uint16_t session_id;
  MessageType message_type;
} __attribute__((packed)) SessionHeader;

/*!
 * \brief CRT communication session management class.
 * Assumes the following properties provided by the underlying transport:
 *  - in-order delivery.
 *  - reliable delivery.
 *
 * Specifically, designed for use with UARTs. Will probably work over semihosting and USB; will
 * probably not work reliably enough over UDP.
 */
class Session {
 public:
  /*! \brief Callback invoked when a full message is received.
   *
   * Note that this function is called for any message with type other than kStartSessionMessage.
   */
  typedef void(*MessageReceivedFunc)(void*, MessageType, Buffer*);

  Session(uint8_t initial_session_nonce, Framer* framer,
          Buffer* receive_buffer,  MessageReceivedFunc message_received_func,
          void* message_received_func_context) :
      nonce_{initial_session_nonce}, state_{State::kReset}, session_id_{0}, receiver_{this},
      framer_{framer}, receive_buffer_{receive_buffer},
      receive_buffer_has_complete_message_{false},
      message_received_func_{message_received_func},
      message_received_func_context_{message_received_func_context} {

        // Session can be used for system startup logging, before the RPC server is instantiated. In
        // this case, allow receive_buffer_ to be nullptr. The instantiator agrees not to use
        // Receiver().
        if (receive_buffer_ != nullptr) {
          receive_buffer_->Clear();
        }
      }

  /*!
   * \brief Start a new session regardless of state. Sends kStartSessionMessage.
   * \return 0 on success, negative error code on failure.
   */
  int StartSession();

  /*!
   * \brief Obtain a WriteStream implementation for use by the framing layer.
   * \return A WriteStream to which received data should be written. Owned by this class.
   */
  WriteStream* Receiver() {
    return &receiver_;
  }

  /*!
   * \brief Send a full message including header, payload, and CRC footer.
   * \param message_type One of MessageType; distinguishes the type of traffic at the session layer.
   * \param message_data The data contained in the message.
   * \param message_size_bytes The number of valid bytes in message_data.
   * \return 0 on success, negative error code on failure.
   */
  int SendMessage(MessageType message_type, const uint8_t* message_data, size_t message_size_bytes);

  /*!
   * \brief Send the framing and session layer headers.
   *
   * This function allows messages to be sent in pieces.
   *
   * \param message_type One of MessageType; distinguishes the type of traffic at the session layer.
   * \param message_size_bytes The size of the message body, in bytes. Excludes the framing and session
   *     layer headers.
   * \return 0 on success, negative error code on failure.
   */
  int StartMessage(MessageType message_type, size_t message_size_bytes);

  /*!
   * \brief Send a part of the message body.
   *
   * This function allows messages to be sent in pieces.
   *
   * \param chunk_data The data contained in this message body chunk.
   * \param chunk_size_bytes The number of valid bytes in chunk_data.
   * \return 0 on success, negative error code on failure.
   */
  int SendBodyChunk(const uint8_t* chunk_data, size_t chunk_size_bytes);

  /*!
   * \brief Finish sending the message by sending the framing layer footer.
   * \return 0 on success, negative error code on failure.
   */
  int FinishMessage();

  /*! \brief Returns true if the session is in the established state. */
  bool IsEstablished() const {
    return state_ == State::kSessionEstablished;
  }

  /*!
   * \brief Clear the receive buffer and prepare to receive next message.
   *
   * Call this function after MessageReceivedFunc is invoked. Any SessionReceiver::Write() calls
   * made will return errors until this function is called to prevent them from corrupting the
   * valid message in the receive buffer.
   */
  void ClearReceiveBuffer();

 private:
  class SessionReceiver : public WriteStream {
   public:
    SessionReceiver(Session* session) : session_{session} {}
    virtual ~SessionReceiver() {}

    ssize_t Write(const uint8_t* data, size_t data_size_bytes) override;
    void PacketDone(bool is_valid) override;

   private:
    void operator delete(void*) noexcept {}
    Session* session_;
  };

  enum class State : uint8_t {
    kReset = 0,
    kStartSessionSent = 1,
    kSessionEstablished = 2,
  };

  void RegenerateNonce();

  int SendInternal(MessageType message_type, const uint8_t* message_data, size_t message_size_bytes);

  void SendSessionStartReply(const SessionHeader& header);

  void ProcessStartSession(const SessionHeader& header);

  uint8_t nonce_;
  State state_;
  uint16_t session_id_;
  SessionReceiver receiver_;
  Framer* framer_;
  Buffer* receive_buffer_;
  bool receive_buffer_has_complete_message_;
  MessageReceivedFunc message_received_func_;
  void* message_received_func_context_;
};

}  // namespace runtime
}  // namespace tvm

#endif  // TVM_RUNTIME_CRT_RPC_SERVER_SESSION_H_