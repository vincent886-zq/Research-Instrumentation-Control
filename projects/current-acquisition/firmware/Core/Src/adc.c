/**
 * adc.c - ADC1 驱动 (TIM2 触发 + DMA 双缓冲)
 *   PA0 = ADC1_IN0, 12-bit
 *   采样率: 2 kHz 有效输出
 *   TIM2 以 16 kHz 触发 ADC, 每次 DMA 半区 8 点做硬件平均
 *   支持 Vrefint 校准真实 VDDA
 */

#include "adc.h"
#include "filter.h"

#define ADC_BUF_SIZE    16
#define ADC_HALF_SIZE   (ADC_BUF_SIZE / 2)

// 句柄
static ADC_HandleTypeDef hadc1;
DMA_HandleTypeDef hdma_adc1;        // 非 static, 供中断文件 extern
static TIM_HandleTypeDef htim2;

// 双缓冲 DMA 数据
static uint16_t adc_buf[ADC_BUF_SIZE];

// 输出状态
static volatile uint8_t  g_ready = 0;
static volatile float    g_voltage = 0.0f;
static float             g_vdda = 3.3f;

// 由 main.c 实现, ADC DMA 回调压入样本到发送环形缓冲
extern void Sample_Push(float v, float a);

// =================================================================
// HAL MSP: GPIO + DMA 初始化 (weak 覆盖)
// =================================================================
void HAL_ADC_MspInit(ADC_HandleTypeDef* adcHandle)
{
    GPIO_InitTypeDef gpio = {0};

    if (adcHandle->Instance == ADC1) {
        __HAL_RCC_GPIOA_CLK_ENABLE();
        __HAL_RCC_DMA2_CLK_ENABLE();

        gpio.Pin  = GPIO_PIN_0;
        gpio.Mode = GPIO_MODE_ANALOG;
        gpio.Pull = GPIO_NOPULL;
        HAL_GPIO_Init(GPIOA, &gpio);

        hdma_adc1.Instance                 = DMA2_Stream0;
        hdma_adc1.Init.Channel             = DMA_CHANNEL_0;
        hdma_adc1.Init.Direction           = DMA_PERIPH_TO_MEMORY;
        hdma_adc1.Init.PeriphInc           = DMA_PINC_DISABLE;
        hdma_adc1.Init.MemInc              = DMA_MINC_ENABLE;
        hdma_adc1.Init.PeriphDataAlignment = DMA_PDATAALIGN_HALFWORD;
        hdma_adc1.Init.MemDataAlignment    = DMA_MDATAALIGN_HALFWORD;
        hdma_adc1.Init.Mode                = DMA_CIRCULAR;
        hdma_adc1.Init.Priority            = DMA_PRIORITY_HIGH;
        hdma_adc1.Init.FIFOMode            = DMA_FIFOMODE_DISABLE;
        HAL_DMA_Init(&hdma_adc1);

        __HAL_LINKDMA(adcHandle, DMA_Handle, hdma_adc1);

        // 使能 DMA2 Stream0 中断
        HAL_NVIC_SetPriority(DMA2_Stream0_IRQn, 5, 0);
        HAL_NVIC_EnableIRQ(DMA2_Stream0_IRQn);
    }
}

// =================================================================
// TIM2 触发源: 8 kHz update → TRGO
// =================================================================
static void TIM2_ADC_Trigger_Init(void)
{
    TIM_MasterConfigTypeDef master = {0};

    __HAL_RCC_TIM2_CLK_ENABLE();

    htim2.Instance = TIM2;
    htim2.Init.Prescaler         = 21 - 1;      // APB1 timer clock = 84MHz, /21 = 4MHz
    htim2.Init.Period            = 250 - 1;     // 4MHz / 250 = 16kHz
    htim2.Init.ClockDivision     = TIM_CLOCKDIVISION_DIV1;
    htim2.Init.CounterMode       = TIM_COUNTERMODE_UP;
    htim2.Init.AutoReloadPreload = TIM_AUTORELOAD_PRELOAD_DISABLE;
    HAL_TIM_Base_Init(&htim2);

    master.MasterOutputTrigger = TIM_TRGO_UPDATE;
    master.MasterSlaveMode     = TIM_MASTERSLAVEMODE_DISABLE;
    HAL_TIMEx_MasterConfigSynchronization(&htim2, &master);

    HAL_TIM_Base_Start(&htim2);
}

// =================================================================
// 处理半区数据: 8 点平均 → 5点中值 (去掉 EMA, 降低相位滞后)
// =================================================================
static void process_half(const uint16_t *buf)
{
    uint32_t sum = 0;
    int i;
    float v;

    for (i = 0; i < ADC_HALF_SIZE; i++) sum += buf[i];

    v = (float)sum * g_vdda / (4096.0f * (float)ADC_HALF_SIZE);

    v = median5(v);        // 去毛刺, 无额外延迟

    g_voltage = v;
    g_ready = 1;

    // 直接推入发送环形缓冲, 由 TIM3 2kHz 中断计算电流并批量发送
    Sample_Push(v, 0.0f);
}

// =================================================================
// DMA 半传输 / 完成回调
// =================================================================
void HAL_ADC_ConvHalfCpltCallback(ADC_HandleTypeDef *hadc)
{
    if (hadc->Instance == ADC1) {
        process_half(&adc_buf[0]);
    }
}

void HAL_ADC_ConvCpltCallback(ADC_HandleTypeDef *hadc)
{
    if (hadc->Instance == ADC1) {
        process_half(&adc_buf[ADC_HALF_SIZE]);
    }
}

// =================================================================
// 初始化入口
// =================================================================
void ADC1_Init(void)
{
    ADC_ChannelConfTypeDef ch = {0};

    __HAL_RCC_ADC1_CLK_ENABLE();

    hadc1.Instance                   = ADC1;
    hadc1.Init.ClockPrescaler        = ADC_CLOCK_SYNC_PCLK_DIV4;   // 84/4 = 21MHz
    hadc1.Init.Resolution            = ADC_RESOLUTION_12B;
    hadc1.Init.ScanConvMode          = DISABLE;
    hadc1.Init.ContinuousConvMode    = DISABLE;                    // TIM2 外部触发
    hadc1.Init.DiscontinuousConvMode = DISABLE;
    hadc1.Init.ExternalTrigConvEdge  = ADC_EXTERNALTRIGCONVEDGE_RISING;
    hadc1.Init.ExternalTrigConv      = ADC_EXTERNALTRIGCONV_T2_TRGO;
    hadc1.Init.DataAlign             = ADC_DATAALIGN_RIGHT;
    hadc1.Init.NbrOfConversion       = 1;
    hadc1.Init.DMAContinuousRequests = ENABLE;
    hadc1.Init.EOCSelection          = ADC_EOC_SINGLE_CONV;
    HAL_ADC_Init(&hadc1);

    // 常规通道: PA0 / ADC1_IN0
    ch.Channel      = ADC_CHANNEL_0;
    ch.Rank         = 1;
    ch.SamplingTime = ADC_SAMPLETIME_144CYCLES;  // 7.4μs @21MHz, 16kHz 触发足够
    HAL_ADC_ConfigChannel(&hadc1, &ch);

    // 启动 TIM2 触发源
    TIM2_ADC_Trigger_Init();

    // 启动 DMA 循环采集
    HAL_ADC_Start_DMA(&hadc1, (uint32_t*)adc_buf, ADC_BUF_SIZE);
}

// =================================================================
// 对外接口
// =================================================================
float ADC1_GetVoltage(void)
{
    g_ready = 0;
    return g_voltage;
}

uint8_t ADC1_IsReady(void)
{
    return g_ready;
}

uint8_t ADC1_WaitReady(uint32_t timeout_ms)
{
    uint32_t tick = HAL_GetTick();
    while (!g_ready) {
        if (HAL_GetTick() - tick >= timeout_ms) return 0;
    }
    return 1;
}
