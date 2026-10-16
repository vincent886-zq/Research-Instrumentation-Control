/**
 * key.h - PA15 按键驱动
 *   KEY 按下 = 低电平, 上拉输入, 10ms 软件消抖
 *   支持: 短按 / 长按 (≥1s)
 */

#ifndef __KEY_H
#define __KEY_H

#include "main.h"

// 按键事件
#define KEY_EVENT_NONE        0
#define KEY_EVENT_SHORT       1   // 短按 (<1s)
#define KEY_EVENT_LONG        2   // 长按 (≥1s)

void     Key_Init(void);
uint8_t  Key_Scan(void);          // 返回事件: 0=无, 1=短按, 2=长按

#endif
