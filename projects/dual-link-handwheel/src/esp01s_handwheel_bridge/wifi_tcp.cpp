#include "wifi_tcp.h"

#include <ESP8266WiFi.h>

#include "bridge_protocol.h"
#include "status_led.h"
#include "uart_frame.h"

namespace WifiTcp {
namespace {

constexpr char kApSsid[] = "CNC_HANDWHEEL";
constexpr char kApPassword[] = "CHANGE_ME_BEFORE_USE";  // Change before deployment; >= 8 chars.
constexpr uint8_t kApChannel = 6;                // Choose 1, 6, or 11 after testing.
constexpr uint16_t kTcpPort = 8266;
const IPAddress kApIp(192, 168, 4, 1);
const IPAddress kApGateway(192, 168, 4, 1);
const IPAddress kApNetmask(255, 255, 255, 0);

constexpr uint16_t kTcpTxQueueSize = 256;
constexpr uint16_t kTcpIoChunk = 64;
constexpr uint8_t kTcpRxRoundsPerLoop = 4;
constexpr uint32_t kTcpStallMs = 300;

WiFiServer gServer(kTcpPort);
WiFiClient gClient;
bool gApReady = false;
uint16_t gActiveSession = 0;
uint16_t gNextSession = 1;

uint8_t gTcpTxQueue[kTcpTxQueueSize];
uint16_t gTcpTxHead = 0;
uint16_t gTcpTxTail = 0;
uint16_t gTcpTxCount = 0;
uint32_t gTcpQueueStartedAt = 0;

void ClearTcpTx() {
  gTcpTxHead = 0;
  gTcpTxTail = 0;
  gTcpTxCount = 0;
  gTcpQueueStartedAt = 0;
}

bool QueueControl(uint8_t type, uint16_t session, uint8_t reason = 0) {
  if (type == BridgeProtocol::kLinkFault) {
    return UartFrame::Queue(type, session, &reason, 1);
  }
  return UartFrame::Queue(type, session, nullptr, 0);
}

void CloseSocket() {
  if (gClient) gClient.stop(1);
  gClient = WiFiClient();
}

// This is the connection-boundary operation: all application queues are
// cleared before STM32 sees the state change.
void EndSession(bool fault, uint8_t reason) {
  const uint16_t departedSession = gActiveSession;
  CloseSocket();
  gActiveSession = 0;
  StatusLed::SetConnected(false);
  ClearTcpTx();
  UartFrame::ClearRx();
  UartFrame::ClearTx();

  if (fault) {
    (void)QueueControl(BridgeProtocol::kLinkFault, departedSession, reason);
  }
  if (departedSession != 0) {
    (void)QueueControl(BridgeProtocol::kTcpDisconnected, departedSession);
  }
}

uint16_t AllocateSession() {
  ++gNextSession;
  if (gNextSession == 0) ++gNextSession;
  return gNextSession;
}

bool PushTcpTx(const uint8_t* data, uint16_t length) {
  if (length > static_cast<uint16_t>(kTcpTxQueueSize - gTcpTxCount)) {
    return false;
  }
  if (length != 0 && gTcpTxCount == 0) gTcpQueueStartedAt = millis();

  while (length-- != 0) {
    gTcpTxQueue[gTcpTxTail] = *data++;
    gTcpTxTail = static_cast<uint16_t>((gTcpTxTail + 1U) % kTcpTxQueueSize);
    ++gTcpTxCount;
  }
  return true;
}

void AcceptOrRejectClient() {
  if (!gServer.hasClient()) return;

  WiFiClient incoming = gServer.accept();
  if (!incoming) return;

  if (gActiveSession != 0 && gClient.connected()) {
    incoming.stop(1);
    return;
  }

  if (gActiveSession != 0) EndSession(false, 0);

  UartFrame::ClearRx();
  UartFrame::ClearTx();
  ClearTcpTx();
  gClient = incoming;
  gClient.setNoDelay(true);
  gClient.keepAlive(2, 1, 2);
  gActiveSession = AllocateSession();
  StatusLed::SetConnected(true);

  if (!QueueControl(BridgeProtocol::kTcpConnected, gActiveSession)) {
    EndSession(true, BridgeProtocol::kFaultUartOverflow);
  }
}

void PollTcpRx() {
  if (gActiveSession == 0 || !gClient) return;

  uint8_t received[kTcpIoChunk];
  uint8_t rounds = 0;
  while (gClient.available() > 0 && rounds++ < kTcpRxRoundsPerLoop) {
    const int available = gClient.available();
    const uint16_t wanted =
        available < static_cast<int>(sizeof(received))
            ? static_cast<uint16_t>(available)
            : static_cast<uint16_t>(sizeof(received));
    const int count = gClient.read(received, wanted);
    if (count <= 0) break;

    if (!UartFrame::Queue(BridgeProtocol::kTcpData, gActiveSession, received,
                          static_cast<uint16_t>(count))) {
      EndSession(true, BridgeProtocol::kFaultTcpRxOverflow);
      return;
    }
  }
}

void FlushTcpTx() {
  if (gTcpTxCount == 0) return;
  if (gActiveSession == 0 || !gClient || !gClient.connected()) {
    EndSession(false, 0);
    return;
  }

  if (static_cast<uint32_t>(millis() - gTcpQueueStartedAt) >= kTcpStallMs) {
    EndSession(true, BridgeProtocol::kFaultTcpStall);
    return;
  }

  const int writable = gClient.availableForWrite();
  if (writable <= 0) return;

  const uint16_t contiguous =
      (gTcpTxTail > gTcpTxHead)
          ? static_cast<uint16_t>(gTcpTxTail - gTcpTxHead)
          : static_cast<uint16_t>(kTcpTxQueueSize - gTcpTxHead);
  const uint16_t writableNow =
      writable > static_cast<int>(kTcpIoChunk)
          ? kTcpIoChunk
          : static_cast<uint16_t>(writable);
  const uint16_t count = contiguous < writableNow ? contiguous : writableNow;
  const size_t sent = gClient.write(&gTcpTxQueue[gTcpTxHead], count);
  if (sent == 0) return;

  gTcpTxHead = static_cast<uint16_t>((gTcpTxHead + sent) % kTcpTxQueueSize);
  gTcpTxCount = static_cast<uint16_t>(gTcpTxCount - sent);
  if (gTcpTxCount == 0) gTcpQueueStartedAt = 0;
}

}  // namespace

bool Begin() {
  // A reboot changes the first allocated session in addition to sending BOOT.
  gNextSession = static_cast<uint16_t>(ESP.getChipId() ^ ESP.getCycleCount() ^
                                       static_cast<uint32_t>(micros()));
  if (gNextSession == 0) gNextSession = 1;

  WiFi.persistent(false);
  WiFi.mode(WIFI_AP);
  WiFi.setSleepMode(WIFI_NONE_SLEEP);
  const bool configured = WiFi.softAPConfig(kApIp, kApGateway, kApNetmask);
  const bool started =
      configured && WiFi.softAP(kApSsid, kApPassword, kApChannel, false, 1);
  gApReady = started;
  if (started) {
    gServer.begin();
    gServer.setNoDelay(true);
  }
  return started;
}

void Poll() {
  if (!gApReady) return;

  if (WiFi.getMode() != WIFI_AP) {
    gApReady = false;
    EndSession(true, BridgeProtocol::kFaultApLost);
    return;
  }

  if (gActiveSession != 0 && !gClient.connected() &&
      gClient.available() == 0) {
    EndSession(false, 0);
  }

  AcceptOrRejectClient();
  PollTcpRx();
  FlushTcpTx();
}

void HandleStmFrame(uint8_t type, uint16_t session, const uint8_t* payload,
                    uint16_t payloadLength) {
  // Old-session and no-client traffic is dropped, never queued for reconnect.
  if (gActiveSession == 0 || session == 0 || session != gActiveSession ||
      !gClient.connected()) {
    return;
  }

  if (type == BridgeProtocol::kControlActivity) {
    if (payloadLength != 0) {
      EndSession(true, BridgeProtocol::kFaultUartFrame);
      return;
    }
    StatusLed::NotifyControlActivity();
    return;
  }

  if (type != BridgeProtocol::kStmToTcp) {
    EndSession(true, BridgeProtocol::kFaultUartFrame);
    return;
  }

  if (!PushTcpTx(payload, payloadLength)) {
    EndSession(true, BridgeProtocol::kFaultTcpTxOverflow);
  }
}

void HandleUartFault(uint8_t reason) { EndSession(true, reason); }

}  // namespace WifiTcp
