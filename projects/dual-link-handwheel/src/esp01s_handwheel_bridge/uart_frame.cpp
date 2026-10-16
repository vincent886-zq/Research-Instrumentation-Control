#include "uart_frame.h"

namespace UartFrame {
namespace {

constexpr uint16_t kSerialRxBufferSize = 256;
constexpr uint16_t kTxQueueSize = 512;
constexpr uint16_t kRxBudgetPerLoop = 256;
constexpr uint32_t kPartialFrameTimeoutMs = 50;

FrameHandler gFrameHandler = nullptr;
FaultHandler gFaultHandler = nullptr;

uint8_t gEncodedRx[BridgeProtocol::kCobsFrameMax];
uint16_t gEncodedRxLength = 0;
uint32_t gPartialFrameStartedAt = 0;

uint8_t gTxQueue[kTxQueueSize];
uint16_t gTxHead = 0;
uint16_t gTxTail = 0;
uint16_t gTxCount = 0;

uint16_t Crc16Ccitt(const uint8_t* data, uint16_t length) {
  uint16_t crc = 0xFFFF;
  while (length-- != 0) {
    crc ^= static_cast<uint16_t>(*data++) << 8;
    for (uint8_t bit = 0; bit < 8; ++bit) {
      crc = (crc & 0x8000U)
                ? static_cast<uint16_t>((crc << 1) ^ 0x1021U)
                : static_cast<uint16_t>(crc << 1);
    }
  }
  return crc;
}

bool PushBytes(const uint8_t* data, uint16_t length) {
  if (length > static_cast<uint16_t>(kTxQueueSize - gTxCount)) return false;
  while (length-- != 0) {
    gTxQueue[gTxTail] = *data++;
    gTxTail = static_cast<uint16_t>((gTxTail + 1U) % kTxQueueSize);
    ++gTxCount;
  }
  return true;
}

bool CobsDecode(const uint8_t* input, uint16_t inputLength, uint8_t* output,
                uint16_t* outputLength) {
  uint16_t read = 0;
  uint16_t write = 0;

  while (read < inputLength) {
    const uint8_t code = input[read++];
    if (code == 0) return false;

    for (uint8_t offset = 1; offset < code; ++offset) {
      if (read >= inputLength || write >= BridgeProtocol::kRawFrameMax) {
        return false;
      }
      output[write++] = input[read++];
    }

    if (code != 0xFFU && read < inputLength) {
      if (write >= BridgeProtocol::kRawFrameMax) return false;
      output[write++] = 0;
    }
  }

  *outputLength = write;
  return true;
}

void ReportFault(uint8_t reason) {
  ClearRx();
  if (gFaultHandler != nullptr) gFaultHandler(reason);
}

void DecodeAndDispatch() {
  uint8_t raw[BridgeProtocol::kRawFrameMax];
  uint16_t rawLength = 0;

  if (!CobsDecode(gEncodedRx, gEncodedRxLength, raw, &rawLength) ||
      rawLength < 8) {
    ReportFault(BridgeProtocol::kFaultUartFrame);
    return;
  }

  const uint16_t payloadLength = static_cast<uint16_t>(raw[4]) |
                                 (static_cast<uint16_t>(raw[5]) << 8);
  if (raw[0] != BridgeProtocol::kVersion ||
      payloadLength > BridgeProtocol::kMaxPayload ||
      rawLength != static_cast<uint16_t>(8U + payloadLength)) {
    ReportFault(BridgeProtocol::kFaultUartFrame);
    return;
  }

  const uint16_t receivedCrc =
      static_cast<uint16_t>(raw[6U + payloadLength]) |
      (static_cast<uint16_t>(raw[7U + payloadLength]) << 8);
  const uint16_t calculatedCrc =
      Crc16Ccitt(raw, static_cast<uint16_t>(6U + payloadLength));
  if (receivedCrc != calculatedCrc) {
    ReportFault(BridgeProtocol::kFaultUartFrame);
    return;
  }

  const uint8_t type = raw[1];
  const uint16_t session = static_cast<uint16_t>(raw[2]) |
                           (static_cast<uint16_t>(raw[3]) << 8);
  if (gFrameHandler != nullptr) {
    gFrameHandler(type, session, &raw[6], payloadLength);
  }
}

}  // namespace

void Begin(uint32_t baud, FrameHandler frameHandler, FaultHandler faultHandler) {
  gFrameHandler = frameHandler;
  gFaultHandler = faultHandler;
  Serial.setRxBufferSize(kSerialRxBufferSize);
  Serial.begin(baud, SERIAL_8N1);
  Serial.setDebugOutput(false);
  Serial.setTimeout(0);
  ClearRx();
  ClearTx();
}

bool Queue(uint8_t type, uint16_t session, const uint8_t* payload,
           uint16_t payloadLength) {
  if (payloadLength > BridgeProtocol::kMaxPayload ||
      (payloadLength != 0 && payload == nullptr)) {
    return false;
  }

  uint8_t raw[BridgeProtocol::kRawFrameMax];
  uint8_t encoded[BridgeProtocol::kCobsFrameMax];
  const uint16_t rawLength = static_cast<uint16_t>(8U + payloadLength);

  raw[0] = BridgeProtocol::kVersion;
  raw[1] = type;
  raw[2] = static_cast<uint8_t>(session & 0xFFU);
  raw[3] = static_cast<uint8_t>(session >> 8);
  raw[4] = static_cast<uint8_t>(payloadLength & 0xFFU);
  raw[5] = static_cast<uint8_t>(payloadLength >> 8);
  for (uint16_t i = 0; i < payloadLength; ++i) raw[6U + i] = payload[i];

  const uint16_t crc =
      Crc16Ccitt(raw, static_cast<uint16_t>(6U + payloadLength));
  raw[6U + payloadLength] = static_cast<uint8_t>(crc & 0xFFU);
  raw[7U + payloadLength] = static_cast<uint8_t>(crc >> 8);

  uint16_t read = 0;
  uint16_t write = 1;
  uint16_t codeIndex = 0;
  uint8_t code = 1;

  while (read < rawLength) {
    const uint8_t value = raw[read++];
    if (value == 0) {
      encoded[codeIndex] = code;
      codeIndex = write++;
      code = 1;
    } else {
      encoded[write++] = value;
      ++code;
      if (code == 0xFFU) {
        encoded[codeIndex] = code;
        codeIndex = write++;
        code = 1;
      }
    }
  }

  encoded[codeIndex] = code;
  encoded[write++] = 0;
  return PushBytes(encoded, write);
}

void PollRx() {
  if (Serial.hasOverrun() || Serial.hasRxError()) {
    while (Serial.available() > 0) (void)Serial.read();
    ReportFault(BridgeProtocol::kFaultUartOverflow);
    return;
  }

  uint16_t budget = kRxBudgetPerLoop;
  while (budget-- != 0 && Serial.available() > 0) {
    const int readValue = Serial.read();
    if (readValue < 0) break;
    const uint8_t value = static_cast<uint8_t>(readValue);

    if (value == 0) {
      if (gEncodedRxLength != 0) DecodeAndDispatch();
      ClearRx();
      continue;
    }

    if (gEncodedRxLength == 0) gPartialFrameStartedAt = millis();
    if (gEncodedRxLength >= BridgeProtocol::kCobsFrameMax) {
      ReportFault(BridgeProtocol::kFaultUartOverflow);
      return;
    }
    gEncodedRx[gEncodedRxLength++] = value;
  }

  if (gEncodedRxLength != 0 &&
      static_cast<uint32_t>(millis() - gPartialFrameStartedAt) >=
          kPartialFrameTimeoutMs) {
    ReportFault(BridgeProtocol::kFaultUartFrame);
  }
}

void FlushTx() {
  int writable = Serial.availableForWrite();
  while (writable > 0 && gTxCount != 0) {
    const uint16_t contiguous =
        (gTxTail > gTxHead)
            ? static_cast<uint16_t>(gTxTail - gTxHead)
            : static_cast<uint16_t>(kTxQueueSize - gTxHead);
    const uint16_t writableNow =
        (writable > static_cast<int>(kTxQueueSize))
            ? kTxQueueSize
            : static_cast<uint16_t>(writable);
    const uint16_t count = contiguous < writableNow ? contiguous : writableNow;
    const size_t sent = Serial.write(&gTxQueue[gTxHead], count);
    if (sent == 0) break;

    gTxHead = static_cast<uint16_t>((gTxHead + sent) % kTxQueueSize);
    gTxCount = static_cast<uint16_t>(gTxCount - sent);
    writable -= static_cast<int>(sent);
  }
}

void ClearRx() {
  gEncodedRxLength = 0;
  gPartialFrameStartedAt = 0;
}

void ClearTx() {
  gTxHead = 0;
  gTxTail = 0;
  gTxCount = 0;
}

}  // namespace UartFrame
