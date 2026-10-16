#include "main.h"
#include "hw_config.h"
#include "ssd1306.h"
#include "oled_font.h"
#include <string.h>

static I2C_HandleTypeDef hi2c1;
static uint8_t display_buffer[1024];

static void SSD1306_CommandList(const uint8_t *commands, uint16_t length)
{
  uint8_t packet[25];
  uint16_t offset = 0U;
  uint16_t chunk;

  while (offset < length)
  {
    chunk = (uint16_t)(length - offset);
    if (chunk > 24U) chunk = 24U;
    packet[0] = 0x00U;
    (void)memcpy(&packet[1], &commands[offset], chunk);
    (void)HAL_I2C_Master_Transmit(&hi2c1, HWHEEL_OLED_I2C_ADDR, packet,
                                  (uint16_t)(chunk + 1U), 25U);
    offset = (uint16_t)(offset + chunk);
  }
}

static void SSD1306_Refresh(void)
{
  uint8_t page;
  uint8_t packet[129];
  uint8_t commands[3];

  packet[0] = 0x40U;
  for (page = 0U; page < 8U; ++page)
  {
    commands[0] = (uint8_t)(0xB0U + page);
    commands[1] = 0x00U;
    commands[2] = 0x10U;
    SSD1306_CommandList(commands, 3U);
    (void)memcpy(&packet[1], &display_buffer[(uint16_t)page * 128U], 128U);
    (void)HAL_I2C_Master_Transmit(&hi2c1, HWHEEL_OLED_I2C_ADDR, packet,
                                  sizeof(packet), 40U);
  }
}

static void SSD1306_DrawChar(uint8_t x, uint8_t page, char c)
{
  uint8_t index;
  uint8_t i;

  if ((x > 122U) || (page > 7U)) return;
  if ((c < ' ') || (c > '~')) c = ' ';
  index = (uint8_t)(c - ' ');
  for (i = 0U; i < 5U; ++i)
    display_buffer[(uint16_t)page * 128U + x + i] = Font5x7[index][i];
  display_buffer[(uint16_t)page * 128U + x + 5U] = 0x00U;
}

static void SSD1306_DrawLine(uint8_t page, const char *text)
{
  uint8_t x = 0U;
  while ((*text != '\0') && (x <= 122U))
  {
    SSD1306_DrawChar(x, page, *text);
    x = (uint8_t)(x + 6U);
    ++text;
  }
}

void SSD1306_Init(void)
{
  const uint8_t init_commands[] = {
    0xAEU, 0x20U, 0x00U, 0x40U, 0xA1U, 0xC8U,
    0x81U, 0x7FU, 0xA6U, 0xA8U, 0x3FU, 0xD3U,
    0x00U, 0xD5U, 0x80U, 0xD9U, 0xF1U, 0xDAU,
    0x12U, 0xDBU, 0x40U, 0x8DU, 0x14U, 0xAFU
  };

  hi2c1.Instance = I2C1;
  hi2c1.Init.ClockSpeed = 100000U;
  hi2c1.Init.DutyCycle = I2C_DUTYCYCLE_2;
  hi2c1.Init.OwnAddress1 = 0U;
  hi2c1.Init.AddressingMode = I2C_ADDRESSINGMODE_7BIT;
  hi2c1.Init.DualAddressMode = I2C_DUALADDRESS_DISABLE;
  hi2c1.Init.OwnAddress2 = 0U;
  hi2c1.Init.GeneralCallMode = I2C_GENERALCALL_DISABLE;
  hi2c1.Init.NoStretchMode = I2C_NOSTRETCH_DISABLE;
  if (HAL_I2C_Init(&hi2c1) != HAL_OK) Error_Handler();

  HAL_Delay(50U);
  SSD1306_CommandList(init_commands, sizeof(init_commands));
  (void)memset(display_buffer, 0, sizeof(display_buffer));
  SSD1306_Refresh();
}

void SSD1306_Show4(const char *line1, const char *line2,
                   const char *line3, const char *line4)
{
  (void)memset(display_buffer, 0, sizeof(display_buffer));
  SSD1306_DrawLine(0U, line1);
  SSD1306_DrawLine(2U, line2);
  SSD1306_DrawLine(4U, line3);
  SSD1306_DrawLine(6U, line4);
  SSD1306_Refresh();
}

void HAL_I2C_MspInit(I2C_HandleTypeDef *i2c)
{
  GPIO_InitTypeDef gpio = {0};
  if (i2c->Instance != I2C1) return;

  __HAL_RCC_GPIOB_CLK_ENABLE();
  __HAL_RCC_I2C1_CLK_ENABLE();
  gpio.Pin = HWHEEL_OLED_SCL_PIN | HWHEEL_OLED_SDA_PIN;
  gpio.Mode = GPIO_MODE_AF_OD;
  gpio.Speed = GPIO_SPEED_FREQ_HIGH;
  HAL_GPIO_Init(GPIOB, &gpio);
}
