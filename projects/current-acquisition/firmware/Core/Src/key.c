/**
 * key.c - PA15 按键驱动实现
 *
 * 消抖方案: 状态机 + 10ms 累积确认
 *   - 每 10ms 调用一次 Key_Scan()
 *   - 连续 3 次低电平 = 确认按下 (30ms)
 *   - 按住 ≥1s 松手 = 长按, <1s = 短按
 *
 * 硬件: PA15 接按键到 GND, 低电平=按下
 *       F407 上直接配 GPIO 即可释放 JTDI 功能, 无需额外寄存器操作
 *       SWD (PA13/PA14) 不受影响, ST-Link 可正常调试
 */

#include "key.h"

#define DEBOUNCE_CNT    3     // 消抖次数 (3×10ms=30ms)
#define LONG_PRESS_MS   1000  // 长按阈值 ms

void Key_Init(void)
{
    GPIO_InitTypeDef gpio = {0};

    // PA15 配置为 GPIO 上拉输入
    // F4 上 GPIO 模式即可自动释放 JTAG 复用, 无需额外写寄存器
    __HAL_RCC_GPIOA_CLK_ENABLE();
    gpio.Pin  = GPIO_PIN_15;
    gpio.Mode = GPIO_MODE_INPUT;
    gpio.Pull = GPIO_PULLUP;
    HAL_GPIO_Init(GPIOA, &gpio);
}

uint8_t Key_Scan(void)
{
    static uint8_t  press_cnt = 0;    // 按下确认计数
    static uint32_t press_tick = 0;   // 按下时刻
    uint8_t state, event = KEY_EVENT_NONE;

    state = (HAL_GPIO_ReadPin(GPIOA, GPIO_PIN_15) == GPIO_PIN_RESET) ? 1 : 0;
    // 注意: KEY 按下 = 低电平 → state=1

    if (state) {
        // 按键按下
        if (++press_cnt >= DEBOUNCE_CNT) {
            if (press_cnt == DEBOUNCE_CNT) {
                press_tick = HAL_GetTick();   // 记录按下时刻
            }
        }
    } else {
        // 按键松开
        if (press_cnt >= DEBOUNCE_CNT) {
            uint32_t duration = HAL_GetTick() - press_tick;
            event = (duration >= LONG_PRESS_MS)
                    ? KEY_EVENT_LONG
                    : KEY_EVENT_SHORT;
        }
        press_cnt = 0;
    }

    return event;
}
