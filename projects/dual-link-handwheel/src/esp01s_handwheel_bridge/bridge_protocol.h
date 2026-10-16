#pragma once

#include <Arduino.h>

namespace BridgeProtocol {

constexpr uint8_t kVersion = 0x01;

enum FrameType : uint8_t {
  kStmToTcp = 0x10,
  kControlActivity = 0x11,
  kTcpConnected = 0x20,
  kTcpDisconnected = 0x21,
  kTcpData = 0x22,
  kLinkFault = 0x23,
};

enum FaultReason : uint8_t {
  kFaultBoot = 1,
  kFaultApStart = 2,
  kFaultUartFrame = 3,
  kFaultUartOverflow = 4,
  kFaultTcpRxOverflow = 5,
  kFaultTcpTxOverflow = 6,
  kFaultTcpStall = 7,
  kFaultApLost = 8,
};

// Decoded frame layout:
// VERSION(1), TYPE(1), SESSION_LE(2), LENGTH_LE(2), PAYLOAD, CRC16_LE(2).
constexpr uint16_t kMaxPayload = 160;
constexpr uint16_t kRawFrameMax = 8 + kMaxPayload;
constexpr uint16_t kCobsFrameMax =
    kRawFrameMax + (kRawFrameMax / 254) + 2;  // COBS overhead + 0 delimiter.

}  // namespace BridgeProtocol
