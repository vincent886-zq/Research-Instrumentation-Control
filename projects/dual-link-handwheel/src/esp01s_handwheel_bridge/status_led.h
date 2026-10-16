#pragma once

#include <Arduino.h>

namespace StatusLed {

void Begin();
void SetConnected(bool connected);
void NotifyControlActivity();
void Update();

}  // namespace StatusLed
