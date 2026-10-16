#ifndef HW_CONFIG_H
#define HW_CONFIG_H

#include "stm32f1xx_hal.h"

/* Verified EC11 RC-board wiring. PB12/PB13 are 5 V-tolerant inputs. */
#define HWHEEL_ENC_A_PORT          GPIOB
#define HWHEEL_ENC_A_PIN           GPIO_PIN_12
#define HWHEEL_ENC_B_PORT          GPIOB
#define HWHEEL_ENC_B_PIN           GPIO_PIN_13

/* Three independent active-low buttons; connect the other terminal to GND. */
#define HWHEEL_AXIS_BUTTON_PORT    GPIOA
#define HWHEEL_AXIS_BUTTON_PIN     GPIO_PIN_0
#define HWHEEL_STEP_UP_PORT        GPIOA
#define HWHEEL_STEP_UP_PIN         GPIO_PIN_1
#define HWHEEL_STEP_DOWN_PORT      GPIOA
#define HWHEEL_STEP_DOWN_PIN       GPIO_PIN_2

/* SSD1306 I2C display. */
#define HWHEEL_OLED_SCL_PORT       GPIOB
#define HWHEEL_OLED_SCL_PIN        GPIO_PIN_6
#define HWHEEL_OLED_SDA_PORT       GPIOB
#define HWHEEL_OLED_SDA_PIN        GPIO_PIN_7
#define HWHEEL_OLED_I2C_ADDR       (0x3CU << 1)

#define HWHEEL_ENCODER_INVERT      0
#define HWHEEL_BUTTON_DEBOUNCE_MS  25U
#define HWHEEL_RECOVERY_HOLD_MS    3000U
#define HWHEEL_HOST_TIMEOUT_MS     1500U
#define HWHEEL_DISPLAY_PERIOD_MS   100U

#endif
