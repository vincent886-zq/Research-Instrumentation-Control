#ifndef SSD1306_H
#define SSD1306_H

#include <stdint.h>

void SSD1306_Init(void);
void SSD1306_Show4(const char *line1, const char *line2,
                   const char *line3, const char *line4);

#endif
