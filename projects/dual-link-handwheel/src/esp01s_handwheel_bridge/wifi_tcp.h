#pragma once

#include <Arduino.h>

namespace WifiTcp {

bool Begin();
void Poll();

void HandleStmFrame(uint8_t type, uint16_t session, const uint8_t* payload,
                    uint16_t payloadLength);
void HandleUartFault(uint8_t reason);

}  // namespace WifiTcp
