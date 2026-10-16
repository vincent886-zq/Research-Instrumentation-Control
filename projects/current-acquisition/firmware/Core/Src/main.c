/**
 * ===========================================================================
 * 程序名称: 8路 HSTS016L 霍尔电流传感器 — AD7606 + USB 虚拟串口 + FreeRTOS
 * ===========================================================================
 *
 * 【程序功能】
 *   STM32F407ZGT6 通过 AD7606 8 通道 16-bit 同步 ADC 同时采集 8 路 HSTS016L,
 *   通过 FSMC 8080 并行总线读取 8 通道样本,
 *   通过 USB CDC 虚拟串口发送至 PC 端串口助手。
 *
 *   采样率: 10 kHz (0.1ms 采一个点), 由 TIM2 硬件 PWM 驱动 CONVST
 *   滤波:   无 (直通原始 16-bit 数据, PC 端后处理)
 *   发送:   FreeRTOS UsbTxTask 逐帧发送二进制格式 (18 字节/帧, 含帧头+序号)
 *         输出率 10 kHz (每通道), 直接发 8 × int16 原始 ADC 码
 *
 *   FreeRTOS 任务:
 *     CmdTask   - 串口命令解析 (最高优先级)
 *     AcqTask   - 从 AD7606 缓冲取数, 打包二进制帧, 入队 USB 发送队列
 *     UsbTxTask - USB 发送二进制帧
 *     KeyTask   - PA15 按键扫描
 *
 *   支持串口命令:
 *     start/s, stop/x — 启停采集
 *     zero/z — 零点校准 (LED常亮, 完成后快闪5次)
 *     stat   — 显示丢数统计
 *     help/h — 帮助
 *
 * 【传感器】 HSTS016L 闭环霍尔电流传感器 × 8
 *   供电: +5V ±5%, ≤12mA | 量程: ±10A (IPN=10A)
 *   零点: 2.5V | 灵敏度: 62.5mV/A | 计算公式: I = (V - Vzero) * 16
 *
 * 【USB 输出格式 (二进制, 10kHz)】
 *   18 字节/帧: 0xAA + 8×int16(小端序) + 帧序号
 *   PC 端自行换算: V = code / 32768 * 5, I = (V - Vzero) * 16
 *
 * ============================================================
 *                      接 线 说 明
 * ============================================================
 *   AD7606 模块:
 *     +5V  → 5V 电源
 *     GND  → GND
 *     VIO  → 3.3V
 *     OS0-OS2 → PB0-PB2
 *     RANGE   → PA7
 *     RESET   → PC0
 *     CONVST  → PA5 (TIM2 CH1 PWM)
 *     BUSY    → PC1 (EXTI1)
 *     RD/NOE  → PD4  (FSMC_NOE)
 *     WR/NWE  → PD5  (FSMC_NWE)
 *     CS      → PG9  (FSMC_NE2)
 *     D0-D15  → PD14/PD15/PD0/PD1/PD8/PD9/PD10 + PE7-PE15 (FSMC_D0-D15)
 *
 *   HSTS016L 传感器 (8 路):
 *     每路传感器接线:
 *       红 → +5V  (8路共用一个 5V 电源, 注意总电流 ≤ 8×12mA = 96mA)
 *       黑 → GND  (8路共地)
 *       黄 → AD7606 对应通道 V1+ ~ V8+
 *       白 → 悬空 (Vref 输出, 不需要接)
 *
 *     AD7606 模拟输入 (单端模式):
 *       V1+ ~ V8+  → 传感器黄线 (信号输出)
 *       V1G ~ V8G  → 全部接 GND (单端模式负端接地, 8路GND连一起)
 *
 *     传感器排列建议:
 *       CH1 (V1):  传感器 #1
 *       CH2 (V2):  传感器 #2
 *       CH3 (V3):  传感器 #3
 *       CH4 (V4):  传感器 #4
 *       CH5 (V5):  传感器 #5
 *       CH6 (V6):  传感器 #6
 *       CH7 (V7):  传感器 #7
 *       CH8 (V8):  传感器 #8
 *
 *   LED:    PC13 板载, 呼吸灯
 *   KEY:    PA15 板载按键
 *   USB:    Micro USB → PC (供电+数据)
 *
 *   灯效:
 *     未采集 — 正弦呼吸
 *     采集中 — 快闪 (ON/OFF 各 200ms)
 *     校准中 — PC13 常亮
 *     校准完 — 快闪 5 次 → 自动回呼吸
 *
 * 【MCU】      STM32F407ZGT6 @168MHz
 * 【编译器】   KEIL MDK-ARM V5
 * 【库】        STM32F4xx HAL Driver + USB Device Library (CDC) + FreeRTOS V9
 * ===========================================================================
 */

#include "main.h"
#include "usb_device.h"
#include "usbd_cdc_if.h"
#include "ad7606.h"
#include "breath.h"
#include "key.h"
#include "stm32f4xx_hal_iwdg.h"
#include "FreeRTOS.h"
#include "task.h"
#include "queue.h"
#include "semphr.h"
#include <string.h>
#include <stdio.h>

// ---------- 传感器参数 (HSTS016L) ----------
#define HSTS016L_IPN    10.0f       /* 额定电流 ±10A */
#define HSTS016L_VZERO  2.5f        /* 零点电压 2.5V */
#define HSTS016L_VFS    0.625f      /* 灵敏度 62.5mV/A */

// ---------- 通道数目 ----------
#define SENSOR_CHANNELS 8

// ---------- AD7606 采样率 ----------
#define AD7606_FS_HZ    10000

// ---------- 校准参数 ----------
#define CAL_SAMPLES     32
#define CAL_WAIT_MS     2000

// ---------- 二进制帧格式 ----------
#define BINARY_FRAME_SYNC   0xAA
#define BINARY_FRAME_SIZE   18          /* 1(同步) + 16(数据) + 1(序号) */

// ---------- USB 发送队列 ----------
#define USB_TX_QUEUE_LEN  64

// ---------- 全局变量 ----------
volatile float g_vzero[SENSOR_CHANNELS] = {
    HSTS016L_VZERO, HSTS016L_VZERO, HSTS016L_VZERO, HSTS016L_VZERO,
    HSTS016L_VZERO, HSTS016L_VZERO, HSTS016L_VZERO, HSTS016L_VZERO
};
volatile uint8_t g_running = 0;

// FreeRTOS 句柄
static TaskHandle_t acq_task_handle = NULL;
static QueueHandle_t usb_tx_queue = NULL;
SemaphoreHandle_t usb_tx_done_sem = NULL;

// IWDG 句柄
IWDG_HandleTypeDef hiwdg;

// TIM3 句柄 (10kHz 触发 AcqTask)
TIM_HandleTypeDef htim3;

// 丢数计数器
static volatile uint32_t g_usb_tx_lost = 0;

// ---------- 校准状态机 ----------
#define CAL_IDLE      0
#define CAL_SAMPLING  1
#define CAL_WAITING   2
#define CAL_FLASHING  3
volatile uint8_t  cal_state  = CAL_IDLE;
volatile uint32_t cal_timer  = 0;
volatile uint8_t  cal_auto_started = 0;

// ---------- 函数声明 ----------
void SystemClock_Config(void);
void TIM3_Init(void);
void TIM3_IRQHandler(void);

float calc_current(uint8_t ch, float v) { return (v - g_vzero[ch]) / HSTS016L_VFS * HSTS016L_IPN; }
static void print_help(void);
static void print_prompt(void)      { USB_printf("> "); }
static void echo_cmd(const char *c) { USB_printf("\r\n[%s]\r\n", c); }
static void process_cmd(const char *cmd);
static void start_calibration(void);

// FreeRTOS 任务
static void CmdTask(void *pvParameters);
static void AcqTask(void *pvParameters);
static void UsbTxTask(void *pvParameters);
static void KeyTask(void *pvParameters);
static void WdgTask(void *pvParameters);

// =================================================================
// 校准 / 命令 / USB 发送
// =================================================================

static void start_calibration(void)
{
    if (cal_state != CAL_IDLE) {
        USB_printf("CAL: Already calibrating, wait...\r\n");
        return;
    }

    cal_state  = CAL_SAMPLING;
    cal_timer  = 0;
    cal_auto_started = 0;

    /* 若未在运行, 临时启动 AD7606 供校准使用 */
    if (!g_running) {
        g_running = 1;
        cal_auto_started = 1;
        AD7606_Start(AD7606_FS_HZ);
    }

    Breath_SetMode(BREATH_SOLID);  // PC13 常亮
    USB_printf("CAL: Calibrating zero for 8 channels...");
}

static void process_cmd(const char *cmd)
{
    if (strcmp(cmd, "start") == 0 || strcmp(cmd, "s") == 0) {
        if (g_running) USB_printf("Already running.\r\n");
        else {
            g_running = 1;
            AD7606_Start(AD7606_FS_HZ);
            USB_printf("Started. Binary output: 0xAA + 8ch int16 raw\r\n");
        }
    }
    else if (strcmp(cmd, "stop") == 0 || strcmp(cmd, "x") == 0) {
        if (!g_running && cal_state == CAL_IDLE) USB_printf("Already stopped.\r\n");
        else {
            g_running = 0;
            cal_state  = CAL_IDLE;
            cal_auto_started = 0;
            AD7606_Stop();
            Breath_SetMode(BREATH_IDLE);
            USB_printf("Stopped.\r\n");
            print_prompt();
        }
    }
    else if (strcmp(cmd, "zero") == 0 || strcmp(cmd, "z") == 0) {
        start_calibration();
    }
    else if (strcmp(cmd, "help") == 0 || strcmp(cmd, "h") == 0) {
        print_help();
        if (!g_running) print_prompt();
    }
    else if (strcmp(cmd, "stat") == 0) {
        USB_printf("Lost: Queue=%lu, Ring=%lu\r\n", g_usb_tx_lost, g_ad7606_lost);
        if (!g_running) print_prompt();
    }
    else if (strlen(cmd) > 0) {
        USB_printf("Unknown. Type 'help'.\r\n");
        if (!g_running) print_prompt();
    }
}

static void print_help(void)
{
    USB_printf("  start/s    begin sampling (8ch)\r\n");
    USB_printf("  stop /x    stop sampling\r\n");
    USB_printf("  zero /z    calibrate zero for 8 channels\r\n");
    USB_printf("  stat       show loss counters (queue/ring overflow)\r\n");
    USB_printf("  help /h    this help\r\n");
    USB_printf("  KEY short  toggle start/stop\r\n");
    USB_printf("  KEY >=1s   calibrate zero (auto)\r\n");
}

// =================================================================
// TIM3 10kHz 中断: 仅通知 AcqTask, 不处理数据
// =================================================================
void TIM3_Init(void)
{
    __HAL_RCC_TIM3_CLK_ENABLE();

    htim3.Instance               = TIM3;
    htim3.Init.Prescaler         = 42 - 1;   // APB1 = 84MHz, /42 = 2MHz
    htim3.Init.Period            = 200 - 1;  // 2MHz / 200 = 10kHz
    htim3.Init.ClockDivision     = TIM_CLOCKDIVISION_DIV1;
    htim3.Init.CounterMode       = TIM_COUNTERMODE_UP;
    htim3.Init.AutoReloadPreload = TIM_AUTORELOAD_PRELOAD_DISABLE;
    HAL_TIM_Base_Init(&htim3);

    HAL_NVIC_SetPriority(TIM3_IRQn, 6, 0);
    HAL_NVIC_EnableIRQ(TIM3_IRQn);
    HAL_TIM_Base_Start_IT(&htim3);
}

void TIM3_IRQHandler(void)
{
    BaseType_t xHigherPriorityTaskWoken = pdFALSE;

    if (__HAL_TIM_GET_FLAG(&htim3, TIM_FLAG_UPDATE)) {
        __HAL_TIM_CLEAR_IT(&htim3, TIM_IT_UPDATE);

        if (acq_task_handle != NULL) {
            vTaskNotifyGiveFromISR(acq_task_handle, &xHigherPriorityTaskWoken);
        }

        portYIELD_FROM_ISR(xHigherPriorityTaskWoken);
    }
}

// =================================================================
// FreeRTOS 任务
// =================================================================

/* 命令处理任务 */
static void CmdTask(void *pvParameters)
{
    char cmd[32];
    uint8_t cmd_idx = 0;
    uint8_t ch;

    (void)pvParameters;

    USB_printf("\r\n=== 8CH HSTS016L Binary Stream (AD7606 + USB CDC + FreeRTOS) ===\r\n");
    USB_printf("F407ZGT6 @168MHz | Binary 18B/frame @ 10kHz | No filter\r\n");
    USB_printf("FRF mode: raw int16 data, PC side processes V/I\r\n\r\n");
    print_help();
    USB_printf("\r\n");
    print_prompt();

    while (1)
    {
        while (USB_GetChar(&ch)) {
            if (ch == '\r' || ch == '\n') {
                if (cmd_idx > 0) {
                    cmd[cmd_idx] = '\0';
                    echo_cmd(cmd);
                    if (g_running && strcmp(cmd,"stop")!=0 && strcmp(cmd,"x")!=0
                                   && strcmp(cmd,"help")!=0 && strcmp(cmd,"h")!=0
                                   && strcmp(cmd,"zero")!=0 && strcmp(cmd,"z")!=0)
                        USB_printf("Busy. Type 'stop' first.\r\n");
                    else
                        process_cmd(cmd);
                    cmd_idx = 0;
                }
            }
            else if (cmd_idx < sizeof(cmd)-1) {
                cmd[cmd_idx++] = (char)ch;
            }
        }

        vTaskDelay(pdMS_TO_TICKS(5));
    }
}

/* 采集任务: 等待 TIM3 通知, 8路滤波后打包发给 UsbTxTask */
static void AcqTask(void *pvParameters)
{
    AD7606_Sample_t s;
    uint32_t last_output_tick = 0;
    int ch;

    (void)pvParameters;

    while (1)
    {
        /* 等待 TIM3 10kHz 通知 */
        ulTaskNotifyTake(pdTRUE, portMAX_DELAY);

        /* ---- 校准采样 (8路同时校准) ---- */
        if (cal_state == CAL_SAMPLING) {
            float sum[SENSOR_CHANNELS];
            int i;

            for (ch = 0; ch < SENSOR_CHANNELS; ch++) sum[ch] = 0.0f;

            for (i = 0; i < CAL_SAMPLES; i++) {
                while (!AD7606_Pop(&s)) {
                    ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(1));
                }
                for (ch = 0; ch < SENSOR_CHANNELS; ch++) {
                    sum[ch] += AD7606_ToVoltage(s.ch[ch], AD7606_RANGE_5V);
                }
            }

            /* 计算每路零点, 并做合理性检验 */
            uint8_t all_ok = 1;
            for (ch = 0; ch < SENSOR_CHANNELS; ch++) {
                g_vzero[ch] = sum[ch] / CAL_SAMPLES;
                if (g_vzero[ch] < 2.2f || g_vzero[ch] > 2.8f) {
                    USB_printf(" CH%d Vzero=%.3fV OUT OF RANGE! (keep default 2.5V)\r\n",
                               ch + 1, g_vzero[ch]);
                    g_vzero[ch] = HSTS016L_VZERO;
                    all_ok = 0;
                }
            }

            if (all_ok) {
                USB_printf(" All 8ch zeros OK.\r\n");
            }

            /* 显示校准结果 */
            USB_printf("   CH1=%.3f  CH2=%.3f  CH3=%.3f  CH4=%.3f\r\n",
                       g_vzero[0], g_vzero[1], g_vzero[2], g_vzero[3]);
            USB_printf("   CH5=%.3f  CH6=%.3f  CH7=%.3f  CH8=%.3f\r\n",
                       g_vzero[4], g_vzero[5], g_vzero[6], g_vzero[7]);
            USB_printf(" (wait 2s...)\r\n");
            cal_state = CAL_WAITING;
            cal_timer = xTaskGetTickCount();
        }

        /* ---- 正常采集: 原始 8×int16 二进制直出 ---- */
        if (g_running && cal_state == CAL_IDLE) {
            while (AD7606_Pop(&s)) {
                uint8_t frame[BINARY_FRAME_SIZE];
                static uint8_t seq = 0;

                frame[0] = BINARY_FRAME_SYNC;                       /* 帧头 0xAA */
                memcpy(&frame[1], s.ch, sizeof(s.ch));              /* 8×int16 原始码 */
                frame[17] = seq++;                                  /* 帧序号 */

                if (xQueueSend(usb_tx_queue, frame, 0) != pdPASS) {
                    g_usb_tx_lost++;
                } else {
                    last_output_tick = xTaskGetTickCount();
                }
            }
        }

        /* ---- 数据管线看门狗 ---- */
        if (g_running && cal_state == CAL_IDLE
            && last_output_tick != 0
            && (xTaskGetTickCount() - last_output_tick) > pdMS_TO_TICKS(2000))
        {
            USB_printf("WARN: No output >2s! Check AD7606/USB.\r\n");
            last_output_tick = xTaskGetTickCount();
        }

        /* ---- 呼吸灯模式 ---- */
        if (cal_state == CAL_IDLE) {
            if (g_running) Breath_SetMode(BREATH_COLLECT);
            else           Breath_SetMode(BREATH_IDLE);
        }

        /* ---- 校准等待 ---- */
        if (cal_state == CAL_WAITING) {
            if (xTaskGetTickCount() - cal_timer >= pdMS_TO_TICKS(CAL_WAIT_MS)) {
                cal_state = CAL_FLASHING;
                cal_timer = xTaskGetTickCount();
                Breath_SetMode(BREATH_CAL_DONE);
            }
        }
        /* ---- 校准完成快闪 ---- */
        else if (cal_state == CAL_FLASHING) {
            if (xTaskGetTickCount() - cal_timer >= pdMS_TO_TICKS(3500)) {
                cal_state = CAL_IDLE;
                USB_printf("CAL: Done.\r\n");

                /* 发送 Vzero 校准结果到上位机: 0xBB + 8×int16(×10000编码) */
                uint8_t vz_frame[BINARY_FRAME_SIZE];
                vz_frame[0] = 0xBB;
                for (int ci = 0; ci < SENSOR_CHANNELS; ci++) {
                    int16_t enc = (int16_t)(g_vzero[ci] * 10000.0f + 0.5f);
                    memcpy(&vz_frame[1 + ci * 2], &enc, sizeof(enc));
                }
                vz_frame[17] = 0;
                xQueueSend(usb_tx_queue, vz_frame, 0);

                if (cal_auto_started) {
                    cal_auto_started = 0;
                    g_running = 0;
                    AD7606_Stop();
                } else {
                    /* 校准前就在采集中, 无需额外操作 */
                }
                if (!g_running) print_prompt();
            }
        }
    }
}

/* USB 发送任务: 从队列取 8 路数据, 批量发送 */
static void UsbTxTask(void *pvParameters)
{
    uint8_t frame[BINARY_FRAME_SIZE];

    (void)pvParameters;

    while (1)
    {
        if (xQueueReceive(usb_tx_queue, frame, portMAX_DELAY) == pdTRUE) {
            /* 重试直到 CDC 空闲 */
            while (CDC_Transmit_FS(frame, BINARY_FRAME_SIZE) != USBD_OK) {
                taskYIELD();
            }
            /* 等待 USB DMA 传输完成, 才能复用 frame 缓冲 */
            xSemaphoreTake(usb_tx_done_sem, portMAX_DELAY);
        }
    }
}

/* 按键任务: 10ms 扫描一次 */
static void KeyTask(void *pvParameters)
{
    (void)pvParameters;

    while (1)
    {
        vTaskDelay(pdMS_TO_TICKS(10));

        uint8_t event = Key_Scan();

        if (event == KEY_EVENT_SHORT) {
            if (g_running || cal_state != CAL_IDLE) {
                g_running = 0;
                cal_state = CAL_IDLE;
                cal_auto_started = 0;
                AD7606_Stop();
                Breath_SetMode(BREATH_IDLE);
                USB_printf("KEY: Stopped.\r\n");
            } else {
                g_running = 1;
                AD7606_Start(AD7606_FS_HZ);
                USB_printf("KEY: Started (binary 10kHz).\r\n");
            }
            if (!g_running) print_prompt();
        }
        else if (event == KEY_EVENT_LONG) {
            start_calibration();
        }
    }
}

/* 看门狗喂狗任务 */
static void WdgTask(void *pvParameters)
{
    (void)pvParameters;

    while (1)
    {
        HAL_IWDG_Refresh(&hiwdg);
        vTaskDelay(pdMS_TO_TICKS(500));
    }
}

// =================================================================
// 主函数
// =================================================================
int main(void)
{
    HAL_Init();
    HAL_NVIC_SetPriority(SysTick_IRQn, 15, 0);
    SystemClock_Config();

    AD7606_Init();
    TIM3_Init();
    Breath_Init();
    Key_Init();

    MX_USB_DEVICE_Init();

    /* 等待 USB 枚举完成 */
    HAL_Delay(1000);

    /* 初始化 IWDG: LSI 32kHz / 256 = 125Hz, 重载 625 → 5 秒超时 */
    hiwdg.Instance = IWDG;
    hiwdg.Init.Prescaler = IWDG_PRESCALER_256;
    hiwdg.Init.Reload   = 625;
    HAL_IWDG_Init(&hiwdg);

    /* 创建队列与信号量 */
    usb_tx_queue    = xQueueCreate(USB_TX_QUEUE_LEN, BINARY_FRAME_SIZE);
    usb_tx_done_sem = xSemaphoreCreateBinary();
    configASSERT(usb_tx_queue != NULL);
    configASSERT(usb_tx_done_sem != NULL);

    /* 初始化 USB_printf 互斥锁 */
    USB_printf_InitMutex();

    /* 创建任务 */
    xTaskCreate(WdgTask,  "Wdg",   128, NULL, 6, NULL);
    xTaskCreate(CmdTask,   "Cmd",   512, NULL, 5, NULL);
    xTaskCreate(AcqTask,   "Acq",   512, NULL, 4, &acq_task_handle);
    xTaskCreate(UsbTxTask, "UsbTx", 512, NULL, 3, NULL);
    xTaskCreate(KeyTask,   "Key",   256, NULL, 2, NULL);

    /* 启动调度器 */
    vTaskStartScheduler();

    while (1) {}
}

// =================================================================
// 系统时钟 + FreeRTOS 钩子
// =================================================================
void SystemClock_Config(void)
{
    RCC_OscInitTypeDef RCC_OscInitStruct = {0};
    RCC_ClkInitTypeDef RCC_ClkInitStruct = {0};

    __HAL_RCC_PWR_CLK_ENABLE();
    __HAL_PWR_VOLTAGESCALING_CONFIG(PWR_REGULATOR_VOLTAGE_SCALE1);

    RCC_OscInitStruct.OscillatorType = RCC_OSCILLATORTYPE_HSE;
    RCC_OscInitStruct.HSEState = RCC_HSE_ON;
    RCC_OscInitStruct.PLL.PLLState = RCC_PLL_ON;
    RCC_OscInitStruct.PLL.PLLSource = RCC_PLLSOURCE_HSE;
    RCC_OscInitStruct.PLL.PLLM = 8;
    RCC_OscInitStruct.PLL.PLLN = 336;
    RCC_OscInitStruct.PLL.PLLP = RCC_PLLP_DIV2;
    RCC_OscInitStruct.PLL.PLLQ = 7;
    if (HAL_RCC_OscConfig(&RCC_OscInitStruct) != HAL_OK) Error_Handler();

    RCC_ClkInitStruct.ClockType = RCC_CLOCKTYPE_HCLK|RCC_CLOCKTYPE_SYSCLK
                                |RCC_CLOCKTYPE_PCLK1|RCC_CLOCKTYPE_PCLK2;
    RCC_ClkInitStruct.SYSCLKSource = RCC_SYSCLKSOURCE_PLLCLK;
    RCC_ClkInitStruct.AHBCLKDivider = RCC_SYSCLK_DIV1;
    RCC_ClkInitStruct.APB1CLKDivider = RCC_HCLK_DIV4;
    RCC_ClkInitStruct.APB2CLKDivider = RCC_HCLK_DIV2;
    if (HAL_RCC_ClockConfig(&RCC_ClkInitStruct, FLASH_LATENCY_5) != HAL_OK) Error_Handler();
}

void Error_Handler(void) { __disable_irq(); while(1){} }

void vApplicationMallocFailedHook(void)
{
    taskDISABLE_INTERRUPTS();
    for(;;);
}

void vApplicationStackOverflowHook(TaskHandle_t xTask, signed char *pcTaskName)
{
    (void)xTask;
    (void)pcTaskName;
    taskDISABLE_INTERRUPTS();
    for(;;);
}
