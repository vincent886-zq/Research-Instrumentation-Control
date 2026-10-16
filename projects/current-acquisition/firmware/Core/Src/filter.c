/**
 * filter.c - 数字滤波实现
 *   5点中值滤波
 *
 *   通道化版本: 8路独立实例, 用于 AD7606
 *   单实例版本: 独立实例, 用于内部 ADC (向后兼容)
 */

#include "filter.h"

// ===================== 通道化版本 (8路) =====================

static struct {
    float   buf[5];
    uint8_t filled;
} m5_ch[MEDIAN5_CH_MAX] = {{0}};

void median5_reset_all(void)
{
    uint8_t i;
    for (i = 0; i < MEDIAN5_CH_MAX; i++) {
        m5_ch[i].filled = 0;
    }
}

void median5_reset_ch(uint8_t ch)
{
    if (ch < MEDIAN5_CH_MAX) {
        m5_ch[ch].filled = 0;
    }
}

float median5_filter(uint8_t ch, float val)
{
    float tmp[5];
    uint8_t i;

    if (ch >= MEDIAN5_CH_MAX) return val;

    if (m5_ch[ch].filled < 5) {
        m5_ch[ch].buf[m5_ch[ch].filled++] = val;
        return val;
    }

    m5_ch[ch].buf[0] = m5_ch[ch].buf[1];
    m5_ch[ch].buf[1] = m5_ch[ch].buf[2];
    m5_ch[ch].buf[2] = m5_ch[ch].buf[3];
    m5_ch[ch].buf[3] = m5_ch[ch].buf[4];
    m5_ch[ch].buf[4] = val;

    for (i = 0; i < 5; i++) tmp[i] = m5_ch[ch].buf[i];
    for (i = 1; i < 5; i++) {
        float key = tmp[i];
        int8_t j = (int8_t)i - 1;
        while (j >= 0 && tmp[j] > key) {
            tmp[j + 1] = tmp[j];
            j--;
        }
        tmp[j + 1] = key;
    }
    return tmp[2];
}

// ===================== 单实例版本 (向后兼容) =====================

static struct {
    float   buf[5];
    uint8_t filled;
} m5_single = {{0}, 0};

void median5_reset(void)
{
    m5_single.filled = 0;
}

float median5(float val)
{
    float tmp[5];
    uint8_t i;

    if (m5_single.filled < 5) {
        m5_single.buf[m5_single.filled++] = val;
        return val;
    }

    m5_single.buf[0] = m5_single.buf[1];
    m5_single.buf[1] = m5_single.buf[2];
    m5_single.buf[2] = m5_single.buf[3];
    m5_single.buf[3] = m5_single.buf[4];
    m5_single.buf[4] = val;

    for (i = 0; i < 5; i++) tmp[i] = m5_single.buf[i];
    for (i = 1; i < 5; i++) {
        float key = tmp[i];
        int8_t j = (int8_t)i - 1;
        while (j >= 0 && tmp[j] > key) {
            tmp[j + 1] = tmp[j];
            j--;
        }
        tmp[j + 1] = key;
    }
    return tmp[2];
}
