#include "bridge_protocol.h"
#include "status_led.h"
#include "uart_frame.h"
#include "wifi_tcp.h"

namespace {

constexpr uint32_t kUartBaud = 115200UL;

void OnStmFrame(uint8_t type, uint16_t session, const uint8_t* payload,
                uint16_t payloadLength) {
  WifiTcp::HandleStmFrame(type, session, payload, payloadLength);
}

void OnUartFault(uint8_t reason) { WifiTcp::HandleUartFault(reason); }

}  // namespace

void setup() {
  StatusLed::Begin();
  UartFrame::Begin(kUartBaud, OnStmFrame, OnUartFault);

  const bool apStarted = WifiTcp::Begin();
  const uint8_t reason = apStarted ? BridgeProtocol::kFaultBoot
                                   : BridgeProtocol::kFaultApStart;
  (void)UartFrame::Queue(BridgeProtocol::kLinkFault, 0, &reason, 1);
}

void loop() {
  UartFrame::PollRx();
  WifiTcp::Poll();
  UartFrame::FlushTx();
  StatusLed::Update();
  yield();
}
