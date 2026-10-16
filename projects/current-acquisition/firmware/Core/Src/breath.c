/**
 * breath.c — 呼吸灯 (TIM6 40kHz, PC13 软件PWM)
 *
 *  IDLE     : 吸1.0s→持亮0.3s→呼1.0s→持暗0.3s, 正弦
 *  COLLECT  : 吸0.5s→持亮0.15s→呼0.5s→持暗0.15s, 正弦 (IDLE一半)
 *  SOLID    : 常亮
 *  CAL_DONE : 快闪 5 次 (ON/OFF 各150ms) → 自动回 IDLE
 */

#include "breath.h"

#define PRESCALER  0
#define PERIOD     2099

// 256点正弦表 (sin→[3,253])
static const uint8_t wave[256] = {
    128,131,134,137,140,143,146,149,152,155,
    158,161,164,167,170,173,176,179,181,184,
    187,190,192,195,197,200,202,205,207,210,
    212,214,216,219,221,223,225,227,228,230,
    232,234,235,237,238,240,241,242,243,245,
    246,247,248,248,249,250,251,251,252,252,
    252,253,253,253,253,253,253,253,252,252,
    252,251,251,250,249,248,248,247,246,245,
    243,242,241,240,238,237,235,234,232,230,
    228,227,225,223,221,219,216,214,212,210,
    207,205,202,200,197,195,192,190,187,184,
    181,179,176,173,170,167,164,161,158,155,
    152,149,146,143,140,137,134,131,128,125,
    122,119,116,113,110,107,104,101, 98, 95,
     92, 89, 86, 83, 80, 77, 75, 72, 69, 66,
     64, 61, 59, 56, 54, 51, 49, 46, 44, 42,
     40, 37, 35, 33, 31, 29, 28, 26, 24, 22,
     21, 19, 18, 16, 15, 14, 13, 11, 10,  9,
      8,  8,  7,  6,  5,  5,  4,  4,  4,  3,
      3,  3,  3,  3,  3,  3,  4,  4,  4,  5,
      5,  6,  7,  8,  8,  9, 10, 11, 13, 14,
     15, 16, 18, 19, 21, 22, 24, 26, 28, 29,
     31, 33, 35, 37, 40, 42, 44, 46, 49, 51,
     54, 56, 59, 61, 64, 66, 69, 72, 75, 77,
     80, 83, 86, 89, 92, 95, 98,101,104,107,
    110,113,116,119,122,125,
};

#define SIN_TROUGH  192
#define SIN_PEAK     64
#define SIN_HALF    128
#define BR_MAX      253
#define BR_MIN        3

// 相位时长 (ms)
#define IDLE_INHALE     800
#define IDLE_HOLD_HI    200
#define IDLE_EXHALE     800
#define IDLE_HOLD_LO    200

// CAL_DONE: 先灭500ms → ON/OFF 各 250ms × 5次 = 共10相 + 前导暗 = 明显可辨
#define CAL_PAUSE_MS    500
#define CAL_PHASE_MS    250
#define CAL_PHASES       10

// ---- 全局 ----
static TIM_HandleTypeDef      htim6;
static volatile breath_mode_t g_mode       = BREATH_IDLE;
static volatile uint8_t       g_pwm        = 0;
static volatile uint8_t       g_brightness = 0;

// 呼吸阶段状态
static uint32_t g_phase_start;
static uint8_t  g_phase;
static uint16_t g_inhale_ms, g_hold_hi_ms, g_exhale_ms, g_hold_lo_ms;

// CAL_DONE 状态
static uint32_t g_cal_start;
static int8_t   g_cal_cnt;    // -1=前导暗, 0..9=闪烁

// COLLECT 闪烁
static uint32_t g_blink_start;

// =================================================================
static void set_breath_params(uint16_t inh, uint16_t hhi, uint16_t exh, uint16_t hlo)
{
    g_inhale_ms  = inh;
    g_hold_hi_ms = hhi;
    g_exhale_ms  = exh;
    g_hold_lo_ms = hlo;
    g_phase      = 0;
    g_phase_start = HAL_GetTick();
}

void Breath_Init(void)
{
    GPIO_InitTypeDef gpio = {0};
    __HAL_RCC_GPIOC_CLK_ENABLE();
    gpio.Pin   = GPIO_PIN_13;
    gpio.Mode  = GPIO_MODE_OUTPUT_PP;
    gpio.Pull  = GPIO_PULLUP;
    gpio.Speed = GPIO_SPEED_FREQ_HIGH;
    HAL_GPIO_Init(GPIOC, &gpio);
    HAL_GPIO_WritePin(GPIOC, GPIO_PIN_13, GPIO_PIN_SET);

    __HAL_RCC_TIM6_CLK_ENABLE();
    htim6.Instance               = TIM6;
    htim6.Init.Prescaler         = PRESCALER;
    htim6.Init.CounterMode       = TIM_COUNTERMODE_UP;
    htim6.Init.Period            = PERIOD;
    htim6.Init.AutoReloadPreload = TIM_AUTORELOAD_PRELOAD_DISABLE;
    HAL_TIM_Base_Init(&htim6);

    /* 优先级提高到 7, 避免被低优先级任务/中断饿死导致灯常亮 */
    HAL_NVIC_SetPriority(TIM6_DAC_IRQn, 7, 0);
    HAL_NVIC_EnableIRQ(TIM6_DAC_IRQn);
    HAL_TIM_Base_Start_IT(&htim6);

    // 上电即开始呼吸
    set_breath_params(IDLE_INHALE, IDLE_HOLD_HI, IDLE_EXHALE, IDLE_HOLD_LO);
}

void Breath_SetMode(breath_mode_t mode)
{
    if (g_mode == mode && mode < BREATH_SOLID) return;
    g_mode = mode;

    switch (mode) {
    case BREATH_IDLE:
        set_breath_params(IDLE_INHALE, IDLE_HOLD_HI, IDLE_EXHALE, IDLE_HOLD_LO);
        break;
    case BREATH_COLLECT:
        g_blink_start = HAL_GetTick();
        g_brightness = BR_MAX;
        break;
    case BREATH_SOLID:
        g_brightness = BR_MAX;
        break;
    case BREATH_CAL_DONE:
        g_cal_cnt   = -1;   // 前导暗 500ms
        g_cal_start = HAL_GetTick();
        g_brightness = 0;   // 先灭
        break;
    case BREATH_OFF:
        g_brightness = 0;
        break;
    }
}

// =================================================================
void TIM6_DAC_IRQHandler(void)
{
    if (!__HAL_TIM_GET_FLAG(&htim6, TIM_FLAG_UPDATE)) return;
    __HAL_TIM_CLEAR_FLAG(&htim6, TIM_FLAG_UPDATE);

    g_pwm = (g_pwm + 1) & 0xFF;

    uint32_t now = HAL_GetTick();
    uint32_t elap;
    uint32_t raw_idx;

    switch (g_mode) {

    case BREATH_IDLE:
        elap = now - g_phase_start;
        switch (g_phase) {
        case 0: // 吸 — 正弦 暗→亮
            if (elap >= g_inhale_ms) {
                g_phase = 1; g_phase_start = now; g_brightness = BR_MAX;
            } else {
                raw_idx = SIN_TROUGH + (elap * SIN_HALF / g_inhale_ms);
                g_brightness = wave[(uint16_t)raw_idx & 0xFF];
            }
            break;
        case 1: // 持亮
            if (elap >= g_hold_hi_ms) { g_phase = 2; g_phase_start = now; }
            break;
        case 2: // 呼 — 正弦 亮→暗
            if (elap >= g_exhale_ms) {
                g_phase = 3; g_phase_start = now; g_brightness = BR_MIN;
            } else {
                raw_idx = SIN_PEAK + (elap * SIN_HALF / g_exhale_ms);
                g_brightness = wave[(uint16_t)raw_idx & 0xFF];
            }
            break;
        case 3: // 持暗
            if (elap >= g_hold_lo_ms) { g_phase = 0; g_phase_start = now; }
            break;
        }
        break;

    case BREATH_COLLECT:
        // 采集快闪: ON 200ms / OFF 200ms
        if (now - g_blink_start >= 200) {
            g_blink_start = now;
            g_brightness = (g_brightness >= BR_MAX/2) ? BR_MIN : BR_MAX;
        }
        break;

    case BREATH_SOLID:
        break;  // g_brightness 已由 Breath_SetMode 设为 BR_MAX

    case BREATH_CAL_DONE:
        elap = now - g_cal_start;
        if (g_cal_cnt < 0) {
            // 前导暗 500ms
            if (elap >= CAL_PAUSE_MS) {
                g_cal_cnt = 0;
                g_cal_start = now;
                g_brightness = BR_MAX;   // 第一闪: 亮
            }
        } else {
            if (elap >= CAL_PHASE_MS) {
                g_cal_cnt++;
                g_cal_start = now;
                if (g_cal_cnt >= CAL_PHASES) {
                    g_mode       = BREATH_IDLE;
                    set_breath_params(IDLE_INHALE, IDLE_HOLD_HI, IDLE_EXHALE, IDLE_HOLD_LO);
                    break;
                }
                g_brightness = (g_cal_cnt & 1) ? 0 : BR_MAX;
            }
        }
        break;

    case BREATH_OFF:
        g_brightness = 0;
        break;
    }

    if (g_mode != BREATH_OFF && g_pwm < g_brightness)
        GPIOC->BSRR = (uint32_t)GPIO_PIN_13 << 16U;
    else
        GPIOC->BSRR = GPIO_PIN_13;
}
