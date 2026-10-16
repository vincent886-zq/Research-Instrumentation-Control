#include "status_led.h"

namespace StatusLed {
namespace {

// ESP-01S onboard blue LED is commonly GPIO2 and active-low.
constexpr uint8_t kLedPin = 2;
constexpr uint32_t kDisconnectedHalfPeriodMs = 500;
constexpr uint32_t kActivityHalfPeriodMs = 75;
constexpr uint32_t kActivityHoldMs = 600;

bool gConnected = false;
uint32_t gLastActivityAt = 0;

void WriteLed(bool on) { digitalWrite(kLedPin, on ? LOW : HIGH); }

}  // namespace

void Begin() {
  // Keep GPIO2 high while changing it to an output; GPIO2 is also a boot strap.
  digitalWrite(kLedPin, HIGH);
  pinMode(kLedPin, OUTPUT);
  WriteLed(false);
}

void SetConnected(bool connected) {
  gConnected = connected;
  gLastActivityAt = 0;
}

void NotifyControlActivity() {
  if (gConnected) gLastActivityAt = millis();
}

void Update() {
  const uint32_t now = millis();

  if (!gConnected) {
    WriteLed(((now / kDisconnectedHalfPeriodMs) & 1U) == 0U);
    return;
  }

  if (gLastActivityAt != 0 &&
      static_cast<uint32_t>(now - gLastActivityAt) < kActivityHoldMs) {
    WriteLed(((now / kActivityHalfPeriodMs) & 1U) == 0U);
    return;
  }

  WriteLed(true);
}

}  // namespace StatusLed
