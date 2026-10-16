/**
 * ad7606.c - AD7606 8 通道 16-bit 同步采集驱动 (HAL + FSMC 并行接口)
 *
 * 采样流程:
 *   1. TIM2 CH1 (PA5) 输出 PWM, 频率 = 采样率, 低电平脉冲触发 AD7606 开始转换
 *   2. AD7606 所有 8 通道同步采样, 转换期间 BUSY(PC1) 高电平
 *   3. 转换完成后 BUSY 下降沿触发 EXTI1 中断
 *   4. 中断中通过 FSMC 内存映射读取 8 通道结果, 压入环形缓冲
 *
 * 接线 (鹿小班 LXB407ZG-P1 V2.0 → AD7606):
 *   FSMC_D0-D15: PD14/PD15/PD0/PD1/PD8/PD9/PD10 + PE7-PE15
 *   FSMC_NOE:    PD4
 *   FSMC_NWE:    PD5
 *   FSMC_NE2:    PG9
 *   (PE4/PE5 未用, 8080并口无需地址线)
 *   OS0-OS2:     PB0-PB2
 *   CONVST:      PA5 (TIM2 CH1 PWM)
 *   RANGE:       PA7
 *   RESET:       PC0
 *   BUSY:        PC1 (EXTI1)
 */

#include "ad7606.h"
#include <string.h>

// ---------- 引脚宏 (适配鹿小班 LXB407ZG-P1 V2.0) ----------
#define OS0_PIN     GPIO_PIN_0     /* PB0 */
#define OS1_PIN     GPIO_PIN_1     /* PB1 */
#define OS2_PIN     GPIO_PIN_2     /* PB2 */
#define CONVST_PIN  GPIO_PIN_5     /* PA5 (TIM2 CH1 PWM) */
#define RANGE_PIN   GPIO_PIN_7     /* PA7 */

#define OS_PORT     GPIOB
#define CONVST_PORT GPIOA
#define RANGE_PORT  GPIOA

#define RESET_PIN   GPIO_PIN_0     /* PC0 */
#define RESET_PORT  GPIOC

#define BUSY_PIN    GPIO_PIN_1     /* PC1 (EXTI1) */
#define BUSY_PORT   GPIOC

// ---------- FSMC 内存映射地址 (Bank2 NE2 = PG9) ----------
#define AD7606_RESULT_ADDR  0x64000000UL
#define AD7606_RESULT()     *((volatile uint16_t *)AD7606_RESULT_ADDR)

// ---------- 量程对应满量程电压 ----------
#define AD7606_CODE_MAX     32768.0f   /* 16-bit 有符号, 满量程对应 32768 */

// ---------- 句柄 ----------
static SRAM_HandleTypeDef hsram;
static TIM_HandleTypeDef  htim2;

volatile AD7606_Ring_t g_ad7606_ring = {0};
volatile uint32_t     g_ad7606_lost = 0;   /* 环形缓冲溢出计数 */
static AD7606_Range_E  g_range = AD7606_RANGE_5V;
static AD7606_OS_E     g_os = AD7606_OS_NO;
static uint32_t        g_seq = 0;
static uint8_t         g_started = 0;

// ---------- 内部函数声明 ----------
static void AD7606_GPIO_Init(void);
static void AD7606_FSMC_Init(void);
static void AD7606_TIM2_PWM_Init(uint32_t freq_hz);
static void AD7606_EXTI1_Init(void);
static void AD7606_SetOSPins(AD7606_OS_E os);
static inline void AD7606_Pin_Set(GPIO_TypeDef *port, uint16_t pin, uint8_t val);

// =================================================================
// 对外接口
// =================================================================

void AD7606_Init(void)
{
    AD7606_GPIO_Init();
    AD7606_FSMC_Init();

    AD7606_SetOS(AD7606_OS_NO);
    AD7606_SetRange(AD7606_RANGE_5V);
    AD7606_Reset();

    /* CONVST 平时高电平 */
    AD7606_Pin_Set(CONVST_PORT, CONVST_PIN, 1);
}

void AD7606_SetOS(AD7606_OS_E os)
{
    g_os = os;
    AD7606_SetOSPins(os);
}

void AD7606_SetRange(AD7606_Range_E range)
{
    g_range = range;
    AD7606_Pin_Set(RANGE_PORT, RANGE_PIN, (range == AD7606_RANGE_10V) ? 1 : 0);
}

void AD7606_Reset(void)
{
    /* RESET 高电平脉冲 >= 50ns */
    AD7606_Pin_Set(RESET_PORT, RESET_PIN, 1);
    __NOP(); __NOP(); __NOP(); __NOP(); __NOP();
    AD7606_Pin_Set(RESET_PORT, RESET_PIN, 0);
}

void AD7606_Start(uint32_t freq_hz)
{
    if (g_started) AD7606_Stop();

    AD7606_Reset();
    AD7606_SetOS(g_os);
    AD7606_SetRange(g_range);

    g_ad7606_ring.wr = 0;
    g_ad7606_ring.rd = 0;
    g_seq = 0;

    AD7606_EXTI1_Init();
    AD7606_TIM2_PWM_Init(freq_hz);

    g_started = 1;
}

void AD7606_Stop(void)
{
    g_started = 0;

    HAL_TIM_PWM_Stop(&htim2, TIM_CHANNEL_1);
    HAL_TIM_Base_Stop(&htim2);
    __HAL_RCC_TIM2_CLK_DISABLE();

    /* BUSY 中断关闭 */
    HAL_NVIC_DisableIRQ(EXTI1_IRQn);
    __HAL_GPIO_EXTI_CLEAR_IT(BUSY_PIN);

    /* CONVST 恢复普通 GPIO 高电平 */
    AD7606_Pin_Set(CONVST_PORT, CONVST_PIN, 1);
}

uint8_t AD7606_Pop(AD7606_Sample_t *out)
{
    uint8_t rd;
    uint8_t wr;
    uint8_t ret = 0;

    /* AD7606 中断优先级高于 FreeRTOS 可管理范围, 必须全关中断保护读指针 */
    __disable_irq();

    rd = g_ad7606_ring.rd;
    wr = g_ad7606_ring.wr;

    if (rd != wr) {
        *out = g_ad7606_ring.buf[rd];
        g_ad7606_ring.rd = (rd + 1) & (AD7606_RING_SIZE - 1);
        ret = 1;
    }

    __enable_irq();
    return ret;
}

float AD7606_ToVoltage(int16_t adc_code, AD7606_Range_E range)
{
    float v_fs = (range == AD7606_RANGE_10V) ? 20.0f : 10.0f;
    return ((float)adc_code / AD7606_CODE_MAX) * (v_fs / 2.0f);
}

void AD7606_BUSY_IRQHandler(void)
{
    if (__HAL_GPIO_EXTI_GET_IT(BUSY_PIN) != RESET) {
        __HAL_GPIO_EXTI_CLEAR_IT(BUSY_PIN);

        AD7606_Sample_t s;
        uint8_t next;

        /* 读取 8 通道 (FSMC 内存映射, 连续 8 次 16-bit 读) */
        s.ch[0] = (int16_t)AD7606_RESULT();
        s.ch[1] = (int16_t)AD7606_RESULT();
        s.ch[2] = (int16_t)AD7606_RESULT();
        s.ch[3] = (int16_t)AD7606_RESULT();
        s.ch[4] = (int16_t)AD7606_RESULT();
        s.ch[5] = (int16_t)AD7606_RESULT();
        s.ch[6] = (int16_t)AD7606_RESULT();
        s.ch[7] = (int16_t)AD7606_RESULT();

        s.timestamp_ms = HAL_GetTick();
        s.seq = (uint8_t)(g_seq++);

        next = (g_ad7606_ring.wr + 1) & (AD7606_RING_SIZE - 1);
        if (next != g_ad7606_ring.rd) {
            g_ad7606_ring.buf[g_ad7606_ring.wr] = s;
            g_ad7606_ring.wr = next;
        } else {
            g_ad7606_lost++;
        }
    }
}

// =================================================================
// 内部实现
// =================================================================

static inline void AD7606_Pin_Set(GPIO_TypeDef *port, uint16_t pin, uint8_t val)
{
    if (val) {
        port->BSRR = pin;                    // 低16位置位
    } else {
        port->BSRR = (uint32_t)pin << 16;   // 高16位复位
    }
}

static void AD7606_SetOSPins(AD7606_OS_E os)
{
    uint8_t os2 = (os >> 2) & 0x01;
    uint8_t os1 = (os >> 1) & 0x01;
    uint8_t os0 = os & 0x01;

    AD7606_Pin_Set(OS_PORT, OS2_PIN, os2);
    AD7606_Pin_Set(OS_PORT, OS1_PIN, os1);
    AD7606_Pin_Set(OS_PORT, OS0_PIN, os0);
}

static void AD7606_GPIO_Init(void)
{
    GPIO_InitTypeDef gpio = {0};

    /* 使能 FSMC 与 GPIO 时钟 */
    __HAL_RCC_FSMC_CLK_ENABLE();
    __HAL_RCC_GPIOD_CLK_ENABLE();
    __HAL_RCC_GPIOE_CLK_ENABLE();
    __HAL_RCC_GPIOG_CLK_ENABLE();
    __HAL_RCC_GPIOA_CLK_ENABLE();
    __HAL_RCC_GPIOB_CLK_ENABLE();
    __HAL_RCC_GPIOC_CLK_ENABLE();

    /* ---- FSMC 数据/控制线 (D0-D15, NOE, NWE, NE2) ---- */
    gpio.Mode = GPIO_MODE_AF_PP;
    gpio.Speed = GPIO_SPEED_FREQ_VERY_HIGH;
    gpio.Pull = GPIO_NOPULL;
    gpio.Alternate = GPIO_AF12_FSMC;

    /* PD: D0, D1, D2, D3, D13, D14, D15, NOE, NWE */
    gpio.Pin = GPIO_PIN_0 | GPIO_PIN_1 | GPIO_PIN_4 | GPIO_PIN_5 |
               GPIO_PIN_8 | GPIO_PIN_9 | GPIO_PIN_10 | GPIO_PIN_14 | GPIO_PIN_15;
    HAL_GPIO_Init(GPIOD, &gpio);

    /* PE: D4-D12, A20, A21 */
    gpio.Pin = GPIO_PIN_4 | GPIO_PIN_5 | GPIO_PIN_7 | GPIO_PIN_8 | GPIO_PIN_9 |
               GPIO_PIN_10 | GPIO_PIN_11 | GPIO_PIN_12 | GPIO_PIN_13 |
               GPIO_PIN_14 | GPIO_PIN_15;
    HAL_GPIO_Init(GPIOE, &gpio);

    /* PG: NE2 (PG9) */
    gpio.Pin = GPIO_PIN_9;
    HAL_GPIO_Init(GPIOG, &gpio);

    /* ---- 控制 GPIO 输出 ---- */
    gpio.Mode = GPIO_MODE_OUTPUT_PP;
    gpio.Alternate = 0;

    /* PB0-PB2: OS0-OS2 */
    gpio.Pin = OS0_PIN | OS1_PIN | OS2_PIN;
    HAL_GPIO_Init(OS_PORT, &gpio);

    /* PA5: CONVST (初始为 GPIO 输出, PWM 模式下由 TIM2 接管) */
    gpio.Pin = CONVST_PIN;
    HAL_GPIO_Init(CONVST_PORT, &gpio);

    /* PA7: RANGE */
    gpio.Pin = RANGE_PIN;
    HAL_GPIO_Init(RANGE_PORT, &gpio);

    /* PC0: RESET */
    gpio.Pin = RESET_PIN;
    HAL_GPIO_Init(RESET_PORT, &gpio);

    /* ---- BUSY 输入 ---- */
    gpio.Mode = GPIO_MODE_INPUT;
    gpio.Pin = BUSY_PIN;
    gpio.Pull = GPIO_NOPULL;
    HAL_GPIO_Init(BUSY_PORT, &gpio);
}

static void AD7606_FSMC_Init(void)
{
    FSMC_NORSRAM_TimingTypeDef timing = {0};

    hsram.Instance = FSMC_NORSRAM_DEVICE;
    hsram.Extended = FSMC_NORSRAM_EXTENDED_DEVICE;

    hsram.Init.NSBank = FSMC_NORSRAM_BANK2;
    hsram.Init.DataAddressMux = FSMC_DATA_ADDRESS_MUX_DISABLE;
    hsram.Init.MemoryType = FSMC_MEMORY_TYPE_SRAM;
    hsram.Init.MemoryDataWidth = FSMC_NORSRAM_MEM_BUS_WIDTH_16;
    hsram.Init.BurstAccessMode = FSMC_BURST_ACCESS_MODE_DISABLE;
    hsram.Init.WaitSignalPolarity = FSMC_WAIT_SIGNAL_POLARITY_LOW;
    hsram.Init.WrapMode = FSMC_WRAP_MODE_DISABLE;
    hsram.Init.WaitSignalActive = FSMC_WAIT_TIMING_BEFORE_WS;
    hsram.Init.WriteOperation = FSMC_WRITE_OPERATION_ENABLE;
    hsram.Init.WaitSignal = FSMC_WAIT_SIGNAL_DISABLE;
    hsram.Init.ExtendedMode = FSMC_EXTENDED_MODE_DISABLE;
    hsram.Init.AsynchronousWait = FSMC_ASYNCHRONOUS_WAIT_DISABLE;
    hsram.Init.WriteBurst = FSMC_WRITE_BURST_DISABLE;
    hsram.Init.ContinuousClock = FSMC_CONTINUOUS_CLOCK_SYNC_ONLY;
    hsram.Init.PageSize = FSMC_PAGE_SIZE_NONE;
#if defined(FSMC_BCR1_WFDIS)
    hsram.Init.WriteFifo = FSMC_WRITE_FIFO_DISABLE;
#endif

    /* AD7606 RD 低电平 >=21ns, 高电平 >=15ns */
    timing.AddressSetupTime = 3;
    timing.AddressHoldTime = 1;
    timing.DataSetupTime = 6;
    timing.BusTurnAroundDuration = 1;
    timing.CLKDivision = 2;       /* 异步模式不使用, 但 HAL 要求 >=2 */
    timing.DataLatency = 2;       /* 异步模式不使用, 但 HAL 要求 >1 */
    timing.AccessMode = FSMC_ACCESS_MODE_A;

    HAL_SRAM_Init(&hsram, &timing, &timing);
}

static void AD7606_TIM2_PWM_Init(uint32_t freq_hz)
{
    TIM_MasterConfigTypeDef master = {0};
    TIM_OC_InitTypeDef sConfigOC = {0};
    GPIO_InitTypeDef gpio = {0};
    uint32_t tim_clk;
    uint32_t period, prescaler, pulse;

    __HAL_RCC_TIM2_CLK_ENABLE();

    /* PA5 -> TIM2_CH1 (AF1) */
    gpio.Pin = CONVST_PIN;
    gpio.Mode = GPIO_MODE_AF_PP;
    gpio.Pull = GPIO_NOPULL;
    gpio.Speed = GPIO_SPEED_FREQ_VERY_HIGH;
    gpio.Alternate = GPIO_AF1_TIM2;
    HAL_GPIO_Init(CONVST_PORT, &gpio);

    /* TIM2 在 APB1, PCLK1 = 42MHz, 定时器时钟 = 2 * PCLK1 = 84MHz */
    tim_clk = HAL_RCC_GetPCLK1Freq() * 2;

    if (freq_hz < 3000) {
        prescaler = 100 - 1;
        period = (tim_clk / 100) / freq_hz - 1;
    } else {
        prescaler = 0;
        period = tim_clk / freq_hz - 1;
    }

    /* 占空比脉冲宽度: 保持 >=25ns 即可; 这里取周期 1/64 */
    if (period >= 64) {
        pulse = period / 64;
    } else {
        pulse = 1;
    }
    if (pulse < 1) pulse = 1;

    htim2.Instance = TIM2;
    htim2.Init.Prescaler = prescaler;
    htim2.Init.Period = period;
    htim2.Init.ClockDivision = TIM_CLOCKDIVISION_DIV1;
    htim2.Init.CounterMode = TIM_COUNTERMODE_UP;
    htim2.Init.AutoReloadPreload = TIM_AUTORELOAD_PRELOAD_ENABLE;
    HAL_TIM_PWM_Init(&htim2);

    master.MasterOutputTrigger = TIM_TRGO_RESET;
    master.MasterSlaveMode = TIM_MASTERSLAVEMODE_DISABLE;
    HAL_TIMEx_MasterConfigSynchronization(&htim2, &master);

    /* PWM1 + 低电平有效: 平时高, 脉冲高电平期间计数 < pulse 时输出低 */
    sConfigOC.OCMode = TIM_OCMODE_PWM1;
    sConfigOC.Pulse = pulse;
    sConfigOC.OCPolarity = TIM_OCPOLARITY_LOW;
    sConfigOC.OCFastMode = TIM_OCFAST_DISABLE;

    HAL_TIM_PWM_ConfigChannel(&htim2, &sConfigOC, TIM_CHANNEL_1);
    HAL_TIM_PWM_Start(&htim2, TIM_CHANNEL_1);
}

static void AD7606_EXTI1_Init(void)
{
    GPIO_InitTypeDef gpio = {0};

    /* PC1 作为 EXTI1 输入, 下降沿触发 */
    gpio.Pin = BUSY_PIN;
    gpio.Mode = GPIO_MODE_IT_FALLING;
    gpio.Pull = GPIO_NOPULL;
    HAL_GPIO_Init(BUSY_PORT, &gpio);

    HAL_NVIC_SetPriority(EXTI1_IRQn, 2, 0);
    HAL_NVIC_EnableIRQ(EXTI1_IRQn);
}
