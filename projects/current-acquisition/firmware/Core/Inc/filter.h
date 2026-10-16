/**
 * filter.h - 数字滤波模块
 *   5点中值滤波 (去脉冲毛刺)
 *
 *   通道化版本:   用于 AD7606 多路传感器 (支持8路独立实例)
 *   单实例版本:   用于 STM32 内部 ADC 等单路场景 (向后兼容)
 */

#ifndef __FILTER_H
#define __FILTER_H

#include <stdint.h>

#define MEDIAN5_CH_MAX  8   /* 支持的最大通道数 */

/* ===================== 通道化版本 (8路AD7606用) ===================== */

/**
 * @brief  通道化中值滤波: 5点取中值, 剔除脉冲毛刺
 * @param  ch   通道索引 0~7
 * @param  val  新采样值
 * @return      中值滤波结果 (前4次返回原始值, 第5次起生效)
 */
float median5_filter(uint8_t ch, float val);

/**
 * @brief  重置指定通道的中值滤波历史
 */
void median5_reset_ch(uint8_t ch);

/**
 * @brief  重置所有通道的中值滤波历史 (采集启停时调用)
 */
void median5_reset_all(void);

/* ===================== 单实例版本 (内部ADC等单路用) ===================== */

/**
 * @brief  单实例中值滤波 (与通道化版本独立状态)
 * @param  val  新采样值
 * @return      中值滤波结果
 */
float median5(float val);

/**
 * @brief  重置单实例中值滤波历史
 */
void median5_reset(void);

#endif
