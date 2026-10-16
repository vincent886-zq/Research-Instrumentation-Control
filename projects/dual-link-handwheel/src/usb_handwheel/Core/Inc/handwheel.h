#ifndef HANDWHEEL_H
#define HANDWHEEL_H

#include <stdint.h>

/* Input-only pendant: this firmware has no motor, spindle or ZMotion output. */
void Handwheel_Init(void);
void Handwheel_Task(void);
void Handwheel_OnEncoderEdge(void);
void Handwheel_UsbReceive(const uint8_t *data, uint32_t length);

#endif
