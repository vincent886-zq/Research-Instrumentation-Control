/**
 * ad7606.h - AD7606 8 通道 16-bit 同步采集驱动 (HAL + FSMC 并行接口)
 *
 * 接口: 8080 并行 (模块默认), 16-bit 数据线, FSMC Bank4 内存映射
 * 控制: PA5/TIM2_CH1 输出 PWM 作为 CONVST 转换触发
 * 中断: PC1/BUSY 下降沿 (EXTI Line1) 触发读取 8 通道
 *
 * 通道 1~8 对应 HSTS016L V1~V8 (单端输入时 Vx- 必须接 GND)
 */

#ifndef __AD7606_H
#define __AD7606_H

#include "main.h"

#ifdef __cplusplus
extern "C" {
#endif

/* 过采样模式 */
typedef enum {
    AD7606_OS_NO = 0,   /* 无过采样, 最高 200 kSps */
    AD7606_OS_X2 = 1,
    AD7606_OS_X4 = 2,
    AD7606_OS_X8 = 3,
    AD7606_OS_X16 = 4,
    AD7606_OS_X32 = 5,
    AD7606_OS_X64 = 6
} AD7606_OS_E;

/* 输入量程 */
typedef enum {
    AD7606_RANGE_5V  = 0,   /* ±5V, 适合 HSTS016L 0~5V 输出 */
    AD7606_RANGE_10V = 1    /* ±10V */
} AD7606_Range_E;

/* 8 通道原始 ADC 值 (有符号 16-bit) */
typedef struct {
    int16_t ch[8];
    uint32_t timestamp_ms;
    uint8_t  seq;           /* 帧序号 */
} AD7606_Sample_t;

/* 环形缓冲 */
#define AD7606_RING_SIZE 64

typedef struct {
    AD7606_Sample_t buf[AD7606_RING_SIZE];
    volatile uint8_t wr;
    volatile uint8_t rd;
} AD7606_Ring_t;

extern volatile AD7606_Ring_t g_ad7606_ring;
extern volatile uint32_t     g_ad7606_lost;   /* 环形缓冲溢出计数 */

void AD7606_Init(void);
void AD7606_SetOS(AD7606_OS_E os);
void AD7606_SetRange(AD7606_Range_E range);
void AD7606_Reset(void);
void AD7606_Start(uint32_t freq_hz);
void AD7606_Stop(void);

uint8_t AD7606_Pop(AD7606_Sample_t *out);
float AD7606_ToVoltage(int16_t adc_code, AD7606_Range_E range);

/* BUSY 中断服务程序, 在 stm32f4xx_it.c 中调用 */
void AD7606_BUSY_IRQHandler(void);

#ifdef __cplusplus
}
#endif

#endif /* __AD7606_H */
