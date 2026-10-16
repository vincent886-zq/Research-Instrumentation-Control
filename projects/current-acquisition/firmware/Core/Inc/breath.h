/**
 * breath.h — 呼吸灯
 *
 *   IDLE      : 未采集 — 吸1.0s→持0.3s→呼1.0s→持0.3s (正弦)
 *   COLLECT   : 采集中 — 吸0.5s→持0.15s→呼0.5s→持0.15s (正弦, IDLE的一半)
 *   SOLID     : 常亮 (校准中提示)
 *   CAL_DONE  : 校准完成快闪 5 次 → 自动回 IDLE
 *   OFF       : 灭
 */

#ifndef __BREATH_H
#define __BREATH_H
#include "main.h"

typedef enum {
    BREATH_IDLE     = 0,
    BREATH_COLLECT  = 1,
    BREATH_SOLID    = 2,
    BREATH_CAL_DONE = 3,
    BREATH_OFF      = 4
} breath_mode_t;

void Breath_Init(void);
void Breath_SetMode(breath_mode_t mode);

#endif
