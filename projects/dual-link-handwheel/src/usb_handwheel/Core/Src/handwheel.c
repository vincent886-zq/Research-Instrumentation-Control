#include "main.h"
#include "handwheel.h"
#include "hw_config.h"
#include "ssd1306.h"
#include "usbd_cdc_if.h"
#include <stdio.h>
#include <string.h>

#define AXIS_COUNT             3U
#define STEP_COUNT             5U
#define ENCODER_QUEUE_LENGTH  32U
#define TX_QUEUE_LENGTH       16U
#define TX_LINE_LENGTH        64U
#define RX_RING_LENGTH       128U
#define RX_LINE_LENGTH        64U
#define ARM_TOKEN_MAX_LENGTH  20U

typedef struct
{
  GPIO_TypeDef *port;
  uint16_t pin;
  GPIO_PinState raw;
  GPIO_PinState stable;
  uint32_t changed_at;
} ButtonState;

static const char axis_names[AXIS_COUNT] = {'X', 'Y', 'Z'};
static const uint16_t step_um[STEP_COUNT] = {10U, 50U, 100U, 500U, 1000U};
static const char *const step_names[STEP_COUNT] = {
  "0.010 mm", "0.050 mm", "0.100 mm", "0.500 mm", "1.000 mm"
};

/* Same complete-sequence decoder as the physically verified EC11 baseline. */
static const uint8_t valid_transition[16] = {
  0U, 1U, 1U, 0U,
  1U, 0U, 0U, 1U,
  1U, 0U, 0U, 1U,
  0U, 1U, 1U, 0U
};

static ButtonState axis_button;
static ButtonState step_up_button;
static ButtonState step_down_button;
static uint8_t axis_index;
static uint8_t step_index;
static int8_t last_direction;
static volatile uint8_t pc_online;
static volatile uint8_t handwheel_armed;
static uint32_t host_seen_at;
static uint32_t axis_button_held_at;
static uint8_t axis_button_recovery_fired;
static uint8_t axis_index_before_recovery_hold;
static uint8_t recovery_rearm_prompt;
static uint32_t input_sequence;

static volatile uint8_t rotary_state;
static volatile uint16_t rotary_history;
static volatile int8_t encoder_queue[ENCODER_QUEUE_LENGTH];
static volatile uint8_t encoder_head;
static volatile uint8_t encoder_tail;
static volatile uint32_t encoder_drop_count;

static char tx_queue[TX_QUEUE_LENGTH][TX_LINE_LENGTH];
static uint8_t tx_head;
static uint8_t tx_tail;
static uint8_t tx_count;
static uint32_t tx_drop_count;

static volatile uint8_t rx_ring[RX_RING_LENGTH];
static volatile uint8_t rx_head;
static volatile uint8_t rx_tail;
static volatile uint32_t rx_drop_count;
static uint32_t rx_drop_reported;
static char rx_line[RX_LINE_LENGTH];
static uint8_t rx_length;
static uint8_t rx_line_overflow;

static uint8_t display_dirty;
static uint32_t display_updated_at;

static void Inputs_Init(void);
static void Button_Init(ButtonState *button, GPIO_TypeDef *port, uint16_t pin);
static uint8_t Button_Pressed(ButtonState *button, uint32_t now);
static uint8_t ReadQuadratureFast(void);
static void EncoderPushFromIRQ(int8_t direction);
static uint8_t EncoderPop(int8_t *direction);
static uint8_t QueueLine(const char *line);
static void QueueStatus(void);
static void QueueHealth(void);
static void QueueDebugInputs(void);
static void ProcessTransmit(void);
static void ProcessReceive(void);
static void ProcessHostLine(const char *line);
static void UpdateDisplay(void);
static void SelectNextAxis(void);
static void ChangeStep(int8_t direction);
static void EmitRotation(int8_t direction);
static void ClearEncoderQueue(void);
static void ClearTransmitQueue(void);
static void Handwheel_Disarm(void);
static void Handwheel_ResetHealthAndSession(void);
static void RecoveryRearmChoice(uint8_t accepted);
static uint8_t ArmTokenValid(const char *token);
static void Handwheel_Arm(const char *token);

void Handwheel_Init(void)
{
  Inputs_Init();
  axis_index = 0U;
  step_index = 2U; /* 0.100 mm */
  last_direction = 0;
  pc_online = 0U;
  handwheel_armed = 0U;
  host_seen_at = 0U;
  axis_button_held_at = 0U;
  axis_button_recovery_fired = 0U;
  axis_index_before_recovery_hold = axis_index;
  recovery_rearm_prompt = 0U;
  input_sequence = 0U;

  rotary_state = ReadQuadratureFast();
  rotary_history = 0U;
  encoder_head = encoder_tail = 0U;
  encoder_drop_count = 0U;

  tx_head = tx_tail = tx_count = 0U;
  tx_drop_count = 0U;
  rx_head = rx_tail = 0U;
  rx_drop_count = rx_drop_reported = 0U;
  rx_length = 0U;
  rx_line_overflow = 0U;

  SSD1306_Init();
  display_dirty = 1U;
  display_updated_at = HAL_GetTick() - HWHEEL_DISPLAY_PERIOD_MS;
  UpdateDisplay();

  (void)QueueLine("HELLO,HWHEEL,4\r\n");
  QueueStatus();
}

void Handwheel_Task(void)
{
  uint32_t now = HAL_GetTick();
  int8_t direction;
  uint8_t budget = 8U;

  ProcessReceive();

  if (pc_online && ((now - host_seen_at) > HWHEEL_HOST_TIMEOUT_MS))
  {
    pc_online = 0U;
    Handwheel_Disarm();
    QueueStatus();
    display_dirty = 1U;
  }

  if (Button_Pressed(&axis_button, now))
  {
    axis_button_held_at = now;
    axis_button_recovery_fired = 0U;
    axis_index_before_recovery_hold = axis_index;
    SelectNextAxis();
  }
  if (axis_button.stable == GPIO_PIN_RESET)
  {
    /* Field recovery is intentionally impossible while armed.  It does not
       move an axis: it only creates a fresh, explicitly unarmed session. */
    if (!handwheel_armed && !axis_button_recovery_fired &&
        ((now - axis_button_held_at) >= HWHEEL_RECOVERY_HOLD_MS))
    {
      axis_button_recovery_fired = 1U;
      /* A long press is recovery, not an axis change.  The short-press action
         above is rolled back before its fresh STATUS is announced. */
      axis_index = axis_index_before_recovery_hold;
      Handwheel_ResetHealthAndSession();
      recovery_rearm_prompt = 1U;
      display_dirty = 1U;
    }
  }
  else
  {
    axis_button_held_at = 0U;
    axis_button_recovery_fired = 0U;
  }
  if (Button_Pressed(&step_up_button, now))
  {
    if (recovery_rearm_prompt) RecoveryRearmChoice(0U); /* UP is NO. */
    else ChangeStep(1);
  }
  if (Button_Pressed(&step_down_button, now))
  {
    if (recovery_rearm_prompt) RecoveryRearmChoice(1U); /* DOWN is YES. */
    else ChangeStep(-1);
  }

  while ((budget > 0U) && EncoderPop(&direction))
  {
    EmitRotation(direction);
    --budget;
  }

  if (display_dirty && ((now - display_updated_at) >= HWHEEL_DISPLAY_PERIOD_MS))
    UpdateDisplay();

  ProcessTransmit();
}

void Handwheel_OnEncoderEdge(void)
{
  uint8_t current_ab = ReadQuadratureFast();
  uint8_t transition;

  if (current_ab == (rotary_state & 0x03U)) return;

  rotary_state = (uint8_t)(((rotary_state << 2) | current_ab) & 0x0FU);
  transition = rotary_state;
  if (valid_transition[transition] == 0U) return;

  rotary_history = (uint16_t)((rotary_history << 4) | transition);
  if ((rotary_history & 0x00FFU) == 0x002BU)
  {
#if HWHEEL_ENCODER_INVERT
    if (pc_online && handwheel_armed) EncoderPushFromIRQ(-1);
#else
    if (pc_online && handwheel_armed) EncoderPushFromIRQ(1);
#endif
  }
  else if ((rotary_history & 0x00FFU) == 0x0017U)
  {
#if HWHEEL_ENCODER_INVERT
    if (pc_online && handwheel_armed) EncoderPushFromIRQ(1);
#else
    if (pc_online && handwheel_armed) EncoderPushFromIRQ(-1);
#endif
  }
}

void Handwheel_UsbReceive(const uint8_t *data, uint32_t length)
{
  uint32_t i;
  for (i = 0U; i < length; ++i)
  {
    uint8_t next = (uint8_t)(rx_head + 1U);
    if (next >= RX_RING_LENGTH) next = 0U;
    if (next == rx_tail)
    {
      ++rx_drop_count;
      continue;
    }
    rx_ring[rx_head] = data[i];
    rx_head = next;
  }
}

static void Inputs_Init(void)
{
  GPIO_InitTypeDef gpio = {0};

  __HAL_RCC_GPIOA_CLK_ENABLE();
  __HAL_RCC_GPIOB_CLK_ENABLE();

  /* The 5 V EC11 RC board biases A/B. Disable MCU pulls on FT pins. */
  gpio.Pin = HWHEEL_ENC_A_PIN | HWHEEL_ENC_B_PIN;
  gpio.Mode = GPIO_MODE_IT_RISING_FALLING;
  gpio.Pull = GPIO_NOPULL;
  gpio.Speed = GPIO_SPEED_FREQ_LOW;
  HAL_GPIO_Init(GPIOB, &gpio);

  gpio.Mode = GPIO_MODE_INPUT;
  gpio.Pull = GPIO_PULLUP;
  gpio.Pin = HWHEEL_AXIS_BUTTON_PIN | HWHEEL_STEP_UP_PIN |
             HWHEEL_STEP_DOWN_PIN;
  HAL_GPIO_Init(GPIOA, &gpio);

  Button_Init(&axis_button, HWHEEL_AXIS_BUTTON_PORT, HWHEEL_AXIS_BUTTON_PIN);
  Button_Init(&step_up_button, HWHEEL_STEP_UP_PORT, HWHEEL_STEP_UP_PIN);
  Button_Init(&step_down_button, HWHEEL_STEP_DOWN_PORT, HWHEEL_STEP_DOWN_PIN);

  __HAL_GPIO_EXTI_CLEAR_IT(HWHEEL_ENC_A_PIN | HWHEEL_ENC_B_PIN);
  HAL_NVIC_SetPriority(EXTI15_10_IRQn, 2U, 0U);
  HAL_NVIC_EnableIRQ(EXTI15_10_IRQn);
}

static void Button_Init(ButtonState *button, GPIO_TypeDef *port, uint16_t pin)
{
  button->port = port;
  button->pin = pin;
  button->raw = HAL_GPIO_ReadPin(port, pin);
  button->stable = button->raw;
  button->changed_at = HAL_GetTick();
}

static uint8_t Button_Pressed(ButtonState *button, uint32_t now)
{
  GPIO_PinState sample = HAL_GPIO_ReadPin(button->port, button->pin);
  if (sample != button->raw)
  {
    button->raw = sample;
    button->changed_at = now;
  }

  if ((button->stable != button->raw) &&
      ((now - button->changed_at) >= HWHEEL_BUTTON_DEBOUNCE_MS))
  {
    button->stable = button->raw;
    return (button->stable == GPIO_PIN_RESET) ? 1U : 0U;
  }
  return 0U;
}

static uint8_t ReadQuadratureFast(void)
{
  uint16_t sample = GPIOB->IDR;
  uint8_t a = ((sample & HWHEEL_ENC_A_PIN) != 0U) ? 1U : 0U;
  uint8_t b = ((sample & HWHEEL_ENC_B_PIN) != 0U) ? 1U : 0U;
  return (uint8_t)(a | (uint8_t)(b << 1));
}

static void EncoderPushFromIRQ(int8_t direction)
{
  uint8_t next = (uint8_t)(encoder_head + 1U);
  if (next >= ENCODER_QUEUE_LENGTH) next = 0U;
  if (next == encoder_tail)
  {
    ++encoder_drop_count;
    return;
  }
  encoder_queue[encoder_head] = direction;
  encoder_head = next;
}

static uint8_t EncoderPop(int8_t *direction)
{
  uint8_t tail = encoder_tail;
  if (tail == encoder_head) return 0U;
  *direction = encoder_queue[tail];
  ++tail;
  if (tail >= ENCODER_QUEUE_LENGTH) tail = 0U;
  encoder_tail = tail;
  return 1U;
}

static void ClearEncoderQueue(void)
{
  uint32_t primask = __get_PRIMASK();
  __disable_irq();
  encoder_head = 0U;
  encoder_tail = 0U;
  __set_PRIMASK(primask);
}

static void ClearTransmitQueue(void)
{
  /* Only Handwheel_Task mutates this queue; do not cancel an IN packet that
     USB has already accepted.  The host must still treat DISARM as immediate. */
  tx_head = 0U;
  tx_tail = 0U;
  tx_count = 0U;
}

static void Handwheel_Disarm(void)
{
  handwheel_armed = 0U;
  ClearEncoderQueue();
  ClearTransmitQueue();
  display_dirty = 1U;
}

static void Handwheel_ResetHealthAndSession(void)
{
  /* A recovery never hides queued input: it first discards every pending
     input/output item and then begins a brand-new unarmed host session. */
  Handwheel_Disarm();
  encoder_drop_count = 0U;
  tx_drop_count = 0U;
  rx_drop_count = 0U;
  rx_drop_reported = 0U;
  rx_length = 0U;
  rx_line_overflow = 0U;

  /* The host may already be back after a transient cable disturbance.  This
     optimistic ONLINE indication still expires normally without its PING. */
  pc_online = 1U;
  host_seen_at = HAL_GetTick();
  display_dirty = 1U;

  (void)QueueLine("HELLO,HWHEEL,4\r\n");
  QueueStatus();
  QueueHealth();
  /* Keep this confirmation last so the PC has completed the fresh v4
     HELLO/STATUS/HEALTH gate before it accepts a physical YES/NO choice. */
  (void)QueueLine("HEALTH,RESET,OK\r\n");
}

static void RecoveryRearmChoice(uint8_t accepted)
{
  if (!recovery_rearm_prompt) return;
  recovery_rearm_prompt = 0U;
  display_dirty = 1U;
  (void)QueueLine(accepted ? "RECOVERY,REARM,YES\r\n"
                            : "RECOVERY,REARM,NO\r\n");
}

static uint8_t ArmTokenValid(const char *token)
{
  uint8_t length = 0U;

  while (token[length] != '\0')
  {
    char c = token[length];
    if (!(((c >= '0') && (c <= '9')) || ((c >= 'A') && (c <= 'Z')) ||
          ((c >= 'a') && (c <= 'z')) || (c == '-') || (c == '_')))
      return 0U;
    ++length;
    if (length > ARM_TOKEN_MAX_LENGTH) return 0U;
  }
  return (length > 0U) ? 1U : 0U;
}

static void Handwheel_Arm(const char *token)
{
  char line[TX_LINE_LENGTH];

  /* ARM is a new session boundary: no offline or pre-ARM input may survive. */
  handwheel_armed = 0U;
  ClearEncoderQueue();
  ClearTransmitQueue();
  handwheel_armed = 1U;
  display_dirty = 1U;

  (void)snprintf(line, sizeof(line), "ARMED,%s,%lu\r\n", token,
                 (unsigned long)input_sequence);
  (void)QueueLine(line);
  QueueStatus();
}

static uint8_t QueueLine(const char *line)
{
  uint8_t next;
  if (tx_count >= TX_QUEUE_LENGTH)
  {
    ++tx_drop_count;
    return 0U;
  }
  (void)strncpy(tx_queue[tx_tail], line, TX_LINE_LENGTH - 1U);
  tx_queue[tx_tail][TX_LINE_LENGTH - 1U] = '\0';
  next = (uint8_t)(tx_tail + 1U);
  tx_tail = (next >= TX_QUEUE_LENGTH) ? 0U : next;
  ++tx_count;
  return 1U;
}

static void QueueStatus(void)
{
  char line[TX_LINE_LENGTH];
  (void)snprintf(line, sizeof(line), "STATUS,%c,%u,%u,%u\r\n",
                 axis_names[axis_index], (unsigned int)step_um[step_index],
                 (unsigned int)pc_online, (unsigned int)handwheel_armed);
  (void)QueueLine(line);
}

static void QueueHealth(void)
{
  char health[TX_LINE_LENGTH];
  (void)snprintf(health, sizeof(health), "HEALTH,%lu,%lu,%lu\r\n",
                 (unsigned long)encoder_drop_count,
                 (unsigned long)tx_drop_count,
                 (unsigned long)rx_drop_count);
  (void)QueueLine(health);
}

static void QueueDebugInputs(void)
{
  char line[TX_LINE_LENGTH];
  uint8_t a = ((GPIOB->IDR & HWHEEL_ENC_A_PIN) != 0U) ? 1U : 0U;
  uint8_t b = ((GPIOB->IDR & HWHEEL_ENC_B_PIN) != 0U) ? 1U : 0U;
  uint8_t axis = (HAL_GPIO_ReadPin(HWHEEL_AXIS_BUTTON_PORT,
                                  HWHEEL_AXIS_BUTTON_PIN) == GPIO_PIN_SET) ? 1U : 0U;
  uint8_t up = (HAL_GPIO_ReadPin(HWHEEL_STEP_UP_PORT,
                                HWHEEL_STEP_UP_PIN) == GPIO_PIN_SET) ? 1U : 0U;
  uint8_t down = (HAL_GPIO_ReadPin(HWHEEL_STEP_DOWN_PORT,
                                  HWHEEL_STEP_DOWN_PIN) == GPIO_PIN_SET) ? 1U : 0U;
  (void)snprintf(line, sizeof(line),
                 "DEBUG,A=%u,B=%u,AXIS=%u,UP=%u,DOWN=%u,ARM=%u\r\n",
                 (unsigned int)a, (unsigned int)b, (unsigned int)axis,
                 (unsigned int)up, (unsigned int)down,
                 (unsigned int)handwheel_armed);
  (void)QueueLine(line);
}

static void ProcessTransmit(void)
{
  uint8_t next;
  if (tx_count == 0U) return;
  if (CDC_Transmit_FS((uint8_t *)tx_queue[tx_head],
                      (uint16_t)strlen(tx_queue[tx_head])) != USBD_OK) return;
  next = (uint8_t)(tx_head + 1U);
  tx_head = (next >= TX_QUEUE_LENGTH) ? 0U : next;
  --tx_count;
}

static void ProcessReceive(void)
{
  while (rx_tail != rx_head)
  {
    char c = (char)rx_ring[rx_tail];
    uint8_t next = (uint8_t)(rx_tail + 1U);
    rx_tail = (next >= RX_RING_LENGTH) ? 0U : next;

    if ((c == '\r') || (c == '\n'))
    {
      if (rx_line_overflow)
        (void)QueueLine("ERR,LINE_TOO_LONG\r\n");
      else if (rx_length > 0U)
      {
        rx_line[rx_length] = '\0';
        ProcessHostLine(rx_line);
      }
      rx_length = 0U;
      rx_line_overflow = 0U;
    }
    else if (!rx_line_overflow)
    {
      if (rx_length < (RX_LINE_LENGTH - 1U))
        rx_line[rx_length++] = c;
      else
        rx_line_overflow = 1U;
    }
  }

  if (rx_drop_reported != rx_drop_count)
  {
    rx_drop_reported = rx_drop_count;
    rx_length = 0U;
    rx_line_overflow = 0U;
    (void)QueueLine("ERR,RX_OVERFLOW\r\n");
  }
}

static void ProcessHostLine(const char *line)
{
  uint32_t now = HAL_GetTick();
  uint8_t hello_received = (uint8_t)(strcmp(line, "HELLO") == 0);

  if ((hello_received != 0U) || (strcmp(line, "PING") == 0) ||
      (strcmp(line, "STATE,CONNECTED,1") == 0))
  {
    uint8_t was_online = pc_online;
    /* HELLO starts a new host session.  Do not inherit a prior authorization. */
    if ((hello_received != 0U) || !was_online) Handwheel_Disarm();
    pc_online = 1U;
    host_seen_at = now;
    display_dirty = 1U;
    if ((hello_received != 0U) || !was_online)
    {
      (void)QueueLine("HELLO,HWHEEL,4\r\n");
      QueueStatus();
    }
  }
  else if (strcmp(line, "STATE,CONNECTED,0") == 0)
  {
    pc_online = 0U;
    Handwheel_Disarm();
    display_dirty = 1U;
    QueueStatus();
  }
  else if (strncmp(line, "ARM,", 4U) == 0)
  {
    const char *token = &line[4];
    if (!pc_online)
      (void)QueueLine("ERR,ARM_REQUIRES_ONLINE\r\n");
    else if (!ArmTokenValid(token))
      (void)QueueLine("ERR,BAD_ARM_TOKEN\r\n");
    else
      Handwheel_Arm(token);
  }
  else if (strcmp(line, "DISARM") == 0)
  {
    Handwheel_Disarm();
    (void)QueueLine("DISARMED\r\n");
    QueueStatus();
  }
  else if (strcmp(line, "STATUS?") == 0)
  {
    QueueStatus();
  }
  else if (strcmp(line, "DEBUG?") == 0)
  {
    QueueDebugInputs();
  }
  else if (strcmp(line, "HEALTH?") == 0)
  {
    QueueHealth();
  }
  else if (strcmp(line, "HEALTH,RESET") == 0)
  {
    if (!pc_online)
      (void)QueueLine("ERR,HEALTH_RESET_REQUIRES_ONLINE\r\n");
    else if (handwheel_armed)
      (void)QueueLine("ERR,HEALTH_RESET_REQUIRES_DISARM\r\n");
    else
      Handwheel_ResetHealthAndSession();
  }
  else
  {
    (void)QueueLine("ERR,UNKNOWN_COMMAND\r\n");
  }
}

static void UpdateDisplay(void)
{
  char line2[22];
  char line3[22];
  char line4[22];
  const char *direction = "WAIT";
  char direction_text[4];

  if (last_direction > 0)
  {
    (void)snprintf(direction_text, sizeof(direction_text), "%c+",
                   axis_names[axis_index]);
    direction = direction_text;
  }
  else if (last_direction < 0)
  {
    (void)snprintf(direction_text, sizeof(direction_text), "%c-",
                   axis_names[axis_index]);
    direction = direction_text;
  }
  else
  {
    (void)snprintf(direction_text, sizeof(direction_text), "%c?",
                   axis_names[axis_index]);
    direction = direction_text;
  }

  (void)snprintf(line2, sizeof(line2), "PC:%s ARM:%s",
                  pc_online ? "ON" : "OFF",
                  handwheel_armed ? "ON" : "OFF");
  if (recovery_rearm_prompt)
  {
    SSD1306_Show4("USB HANDWHEEL", "RECOVERED ARM:OFF",
                  "REARM? DOWN=YES", "UP=NO");
    display_dirty = 0U;
    display_updated_at = HAL_GetTick();
    return;
  }
  (void)snprintf(line3, sizeof(line3), "AXIS:%c DIR:%s",
                 axis_names[axis_index], direction);
  (void)snprintf(line4, sizeof(line4), "STEP:%s", step_names[step_index]);
  SSD1306_Show4("USB HANDWHEEL", line2, line3, line4);
  display_dirty = 0U;
  display_updated_at = HAL_GetTick();
}

static void SelectNextAxis(void)
{
  char line[TX_LINE_LENGTH];
  ++axis_index;
  if (axis_index >= AXIS_COUNT) axis_index = 0U;
  /* A direction belongs to the currently selected axis only. */
  last_direction = 0;
  if (pc_online && handwheel_armed)
  {
    ++input_sequence;
    (void)snprintf(line, sizeof(line), "CONFIG,%lu,AXIS,%c\r\n",
                   (unsigned long)input_sequence, axis_names[axis_index]);
    (void)QueueLine(line);
    QueueStatus();
  }
  display_dirty = 1U;
}

static void ChangeStep(int8_t direction)
{
  uint8_t previous = step_index;
  char line[TX_LINE_LENGTH];

  if ((direction > 0) && (step_index < (STEP_COUNT - 1U))) ++step_index;
  if ((direction < 0) && (step_index > 0U)) --step_index;
  if (previous == step_index) return;

  if (pc_online && handwheel_armed)
  {
    ++input_sequence;
    (void)snprintf(line, sizeof(line), "CONFIG,%lu,STEP,%u\r\n",
                   (unsigned long)input_sequence,
                   (unsigned int)step_um[step_index]);
    (void)QueueLine(line);
    QueueStatus();
  }
  display_dirty = 1U;
}

static void EmitRotation(int8_t direction)
{
  char line[TX_LINE_LENGTH];
  if (!pc_online || !handwheel_armed) return;
  last_direction = direction;
  ++input_sequence;
  (void)snprintf(line, sizeof(line), "EVENT,%lu,%c,%u,%d\r\n",
                 (unsigned long)input_sequence, axis_names[axis_index],
                 (unsigned int)step_um[step_index], (int)direction);
  (void)QueueLine(line);
  display_dirty = 1U;
}
