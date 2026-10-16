#pragma once

#include <Arduino.h>

#include "bridge_protocol.h"

namespace UartFrame {

using FrameHandler = void (*)(uint8_t type, uint16_t session,
                              const uint8_t* payload, uint16_t payloadLength);
using FaultHandler = void (*)(uint8_t reason);

void Begin(uint32_t baud, FrameHandler frameHandler, FaultHandler faultHandler);
void PollRx();
void FlushTx();

bool Queue(uint8_t type, uint16_t session, const uint8_t* payload,
           uint16_t payloadLength);

// Used at every connection boundary. It prevents bytes from an old TCP session
// from being delivered after a reconnect.
void ClearRx();
void ClearTx();

}  // namespace UartFrame
