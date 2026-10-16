/**
 * adc.h - ADC1 驱动 (TIM2 触发 + DMA 双缓冲)
 *   PA0 = ADC1_IN0, 12-bit
 *   采样率: 1 kHz (TIM2 8kHz 触发, 8点硬件平均 → 1ms 输出1点)
 *   支持内部 Vrefint 校准真实 VDDA
 */

#ifndef __ADC_H
#define __ADC_H

#include "main.h"

// DMA 句柄, 供 stm32f4xx_it.c 中断服务程序使用
extern DMA_HandleTypeDef hdma_adc1;

// 初始化 ADC1 + DMA2_Stream0 + TIM2 触发
void ADC1_Init(void);

// 获取最新滤波后的电压 (V), 并清除 ready 标志
float ADC1_GetVoltage(void);

// 是否有新数据可用
uint8_t ADC1_IsReady(void);

// 等待新数据 (带超时 ms)
uint8_t ADC1_WaitReady(uint32_t timeout_ms);

#endif
