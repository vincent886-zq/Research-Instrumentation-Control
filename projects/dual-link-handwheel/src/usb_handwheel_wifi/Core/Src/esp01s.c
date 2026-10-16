#include "esp01s.h"

#include "handwheel.h"
#include "hw_config.h"

/* STM32 peer of esp01s_handwheel_bridge.  ESP-AT text is deliberately not
 * parsed here: USART1 carries only COBS framed, CRC16 checked bridge data. */

#define ESP_BRIDGE_VERSION                 0x01U
#define ESP_FRAME_STM_TO_TCP                0x10U
#define ESP_FRAME_CONTROL_ACTIVITY          0x11U
#define ESP_FRAME_TCP_CONNECTED             0x20U
#define ESP_FRAME_TCP_DISCONNECTED          0x21U
#define ESP_FRAME_TCP_DATA                  0x22U
#define ESP_FRAME_LINK_FAULT                0x23U

#define ESP_BRIDGE_MAX_PAYLOAD              160U
#define ESP_BRIDGE_RAW_MAX                  (8U + ESP_BRIDGE_MAX_PAYLOAD)
#define ESP_BRIDGE_COBS_MAX                 (ESP_BRIDGE_RAW_MAX + 2U)
#define ESP_UART_RX_RING_LENGTH             256U
#define ESP_UART_TX_RING_LENGTH             256U
#define ESP_PARTIAL_FRAME_TIMEOUT_MS        50U

static volatile uint8_t uart_rx_ring[ESP_UART_RX_RING_LENGTH];
static volatile uint16_t uart_rx_head;
static volatile uint16_t uart_rx_tail;
static volatile uint32_t uart_rx_fault_count;
static uint32_t uart_rx_fault_seen;

static volatile uint8_t uart_tx_ring[ESP_UART_TX_RING_LENGTH];
static volatile uint16_t uart_tx_head;
static volatile uint16_t uart_tx_tail;

static uint8_t encoded_rx[ESP_BRIDGE_COBS_MAX];
static uint16_t encoded_rx_length;
static uint32_t partial_frame_started_at;
static uint16_t active_session;

static uint16_t NextIndex(uint16_t index, uint16_t length);
static uint8_t UartPop(uint8_t *value);
static uint8_t UartQueue(const uint8_t *data, uint16_t length);
static uint16_t UartTxFree(void);
static void ClearUartRx(void);
static void ClearUartTx(void);
static void ResetFrameRx(void);
static void FailBridge(void);
static uint16_t Crc16Ccitt(const uint8_t *data, uint16_t length);
static uint8_t CobsDecode(const uint8_t *input, uint16_t input_length,
                          uint8_t *output, uint16_t *output_length);
static uint8_t QueueBridgeFrame(uint8_t type, uint16_t session,
                                const uint8_t *payload,
                                uint16_t payload_length);
static void HandleBridgeFrame(const uint8_t *encoded, uint16_t encoded_length);
static void HandleIncomingByte(uint8_t value);
static void StartSession(uint16_t session);
static void EndSession(uint16_t session);

void Esp01s_Init(void)
{
  GPIO_InitTypeDef gpio = {0};

  __HAL_RCC_GPIOA_CLK_ENABLE();
  __HAL_RCC_USART1_CLK_ENABLE();

  gpio.Pin = HWHEEL_ESP_TX_PIN;
  gpio.Mode = GPIO_MODE_AF_PP;
  gpio.Speed = GPIO_SPEED_FREQ_HIGH;
  HAL_GPIO_Init(HWHEEL_ESP_GPIO_PORT, &gpio);

  gpio.Pin = HWHEEL_ESP_RX_PIN;
  gpio.Mode = GPIO_MODE_INPUT;
  gpio.Pull = GPIO_NOPULL;
  HAL_GPIO_Init(HWHEEL_ESP_GPIO_PORT, &gpio);

  /* APB2 is 72 MHz. ESP bridge UART is fixed at 115200, 8-N-1. */
  USART1->CR1 = 0U;
  USART1->CR2 = 0U;
  USART1->CR3 = 0U;
  USART1->BRR = (uint32_t)(HAL_RCC_GetPCLK2Freq() / HWHEEL_ESP_BAUD);
  USART1->CR1 = USART_CR1_UE | USART_CR1_TE | USART_CR1_RE |
                USART_CR1_RXNEIE;

  /* Encoder EXTI stays above the communications bridge. */
  HAL_NVIC_SetPriority(USART1_IRQn, 3U, 0U);
  HAL_NVIC_EnableIRQ(USART1_IRQn);

  uart_rx_head = uart_rx_tail = 0U;
  uart_rx_fault_count = uart_rx_fault_seen = 0U;
  uart_tx_head = uart_tx_tail = 0U;
  active_session = 0U;
  ResetFrameRx();
}

void Esp01s_Task(void)
{
  uint8_t value;
  uint32_t now = HAL_GetTick();

  /* An UART error could hide a session boundary or DISARM. Fail closed
   * before parsing any later bytes. */
  if (uart_rx_fault_seen != uart_rx_fault_count)
  {
    uart_rx_fault_seen = uart_rx_fault_count;
    ClearUartRx();
    FailBridge();
    return;
  }

  while (UartPop(&value)) HandleIncomingByte(value);

  if ((encoded_rx_length != 0U) &&
      ((now - partial_frame_started_at) >= ESP_PARTIAL_FRAME_TIMEOUT_MS))
    FailBridge();
}

uint8_t Esp01s_Send(const uint8_t *data, uint16_t length)
{
  uint8_t accepted;
  if ((active_session == 0U) || (data == 0) ||
      (length > ESP_BRIDGE_MAX_PAYLOAD))
    return 0U;

  accepted = QueueBridgeFrame(ESP_FRAME_STM_TO_TCP, active_session, data, length);
  /* A full STM32->ESP UART queue must never become delayed EVENT replay.
   * The only safe recovery is a fresh TCP session and fresh ARM handshake. */
  if (!accepted) FailBridge();
  return accepted;
}

void Esp01s_NotifyControlActivity(void)
{
  /* This only drives ESP GPIO2 feedback. It has no authorization meaning. */
  if (active_session != 0U)
    (void)QueueBridgeFrame(ESP_FRAME_CONTROL_ACTIVITY, active_session, 0, 0U);
}

void Esp01s_OnUartIrq(void)
{
  uint32_t status = USART1->SR;

  if ((status & (USART_SR_ORE | USART_SR_NE | USART_SR_FE | USART_SR_PE)) != 0U)
  {
    (void)USART1->DR;
    ++uart_rx_fault_count;
  }
  else if ((status & USART_SR_RXNE) != 0U)
  {
    uint8_t value = (uint8_t)USART1->DR;
    uint16_t next = NextIndex(uart_rx_head, ESP_UART_RX_RING_LENGTH);
    if (next == uart_rx_tail) ++uart_rx_fault_count;
    else
    {
      uart_rx_ring[uart_rx_head] = value;
      uart_rx_head = next;
    }
  }

  if (((USART1->CR1 & USART_CR1_TXEIE) != 0U) &&
      ((USART1->SR & USART_SR_TXE) != 0U))
  {
    if (uart_tx_head != uart_tx_tail)
    {
      USART1->DR = uart_tx_ring[uart_tx_head];
      uart_tx_head = NextIndex(uart_tx_head, ESP_UART_TX_RING_LENGTH);
    }
    else
      USART1->CR1 &= (uint16_t)~USART_CR1_TXEIE;
  }
}

static uint16_t NextIndex(uint16_t index, uint16_t length)
{
  ++index;
  return (index >= length) ? 0U : index;
}

static uint8_t UartPop(uint8_t *value)
{
  uint16_t tail = uart_rx_tail;
  if (tail == uart_rx_head) return 0U;
  *value = uart_rx_ring[tail];
  uart_rx_tail = NextIndex(tail, ESP_UART_RX_RING_LENGTH);
  return 1U;
}

static uint16_t UartTxFree(void)
{
  uint16_t head = uart_tx_head;
  uint16_t tail = uart_tx_tail;
  if (tail >= head)
    return (uint16_t)(ESP_UART_TX_RING_LENGTH - (tail - head) - 1U);
  return (uint16_t)(head - tail - 1U);
}

static uint8_t UartQueue(const uint8_t *data, uint16_t length)
{
  uint16_t index;
  if ((data == 0) || (length == 0U) || (length > UartTxFree())) return 0U;

  for (index = 0U; index < length; ++index)
  {
    uart_tx_ring[uart_tx_tail] = data[index];
    uart_tx_tail = NextIndex(uart_tx_tail, ESP_UART_TX_RING_LENGTH);
  }
  USART1->CR1 |= USART_CR1_TXEIE;
  return 1U;
}

static void ClearUartRx(void)
{
  uint32_t primask = __get_PRIMASK();
  __disable_irq();
  uart_rx_head = uart_rx_tail = 0U;
  __set_PRIMASK(primask);
}

static void ClearUartTx(void)
{
  uint32_t primask = __get_PRIMASK();
  __disable_irq();
  uart_tx_head = uart_tx_tail = 0U;
  USART1->CR1 &= (uint16_t)~USART_CR1_TXEIE;
  __set_PRIMASK(primask);
}

static void ResetFrameRx(void)
{
  encoded_rx_length = 0U;
  partial_frame_started_at = 0U;
}

static void FailBridge(void)
{
  /* If the ESP cannot report the failure, v4 PING timeout remains a second
   * independent fail-safe. Old UART output is never retained for reconnect. */
  active_session = 0U;
  ResetFrameRx();
  ClearUartTx();
  Handwheel_TransportDisconnected();
}

static uint16_t Crc16Ccitt(const uint8_t *data, uint16_t length)
{
  uint16_t crc = 0xFFFFU;
  uint8_t bit;
  while (length-- != 0U)
  {
    crc ^= (uint16_t)(*data++) << 8;
    for (bit = 0U; bit < 8U; ++bit)
      crc = (crc & 0x8000U) ? (uint16_t)((crc << 1) ^ 0x1021U)
                             : (uint16_t)(crc << 1);
  }
  return crc;
}

static uint8_t CobsDecode(const uint8_t *input, uint16_t input_length,
                          uint8_t *output, uint16_t *output_length)
{
  uint16_t read = 0U;
  uint16_t write = 0U;

  while (read < input_length)
  {
    uint8_t code = input[read++];
    uint8_t offset;
    if (code == 0U) return 0U;
    for (offset = 1U; offset < code; ++offset)
    {
      if ((read >= input_length) || (write >= ESP_BRIDGE_RAW_MAX)) return 0U;
      output[write++] = input[read++];
    }
    if ((code != 0xFFU) && (read < input_length))
    {
      if (write >= ESP_BRIDGE_RAW_MAX) return 0U;
      output[write++] = 0U;
    }
  }

  *output_length = write;
  return 1U;
}

static uint8_t QueueBridgeFrame(uint8_t type, uint16_t session,
                                const uint8_t *payload,
                                uint16_t payload_length)
{
  uint8_t raw[ESP_BRIDGE_RAW_MAX];
  uint8_t encoded[ESP_BRIDGE_COBS_MAX];
  uint16_t raw_length;
  uint16_t read;
  uint16_t write = 1U;
  uint16_t code_index = 0U;
  uint8_t code = 1U;
  uint16_t crc;

  if ((payload_length > ESP_BRIDGE_MAX_PAYLOAD) ||
      ((payload_length != 0U) && (payload == 0)))
    return 0U;

  raw_length = (uint16_t)(8U + payload_length);
  raw[0] = ESP_BRIDGE_VERSION;
  raw[1] = type;
  raw[2] = (uint8_t)(session & 0xFFU);
  raw[3] = (uint8_t)(session >> 8);
  raw[4] = (uint8_t)(payload_length & 0xFFU);
  raw[5] = (uint8_t)(payload_length >> 8);
  for (read = 0U; read < payload_length; ++read) raw[6U + read] = payload[read];

  crc = Crc16Ccitt(raw, (uint16_t)(6U + payload_length));
  raw[6U + payload_length] = (uint8_t)(crc & 0xFFU);
  raw[7U + payload_length] = (uint8_t)(crc >> 8);

  for (read = 0U; read < raw_length; ++read)
  {
    uint8_t value = raw[read];
    if (value == 0U)
    {
      encoded[code_index] = code;
      code_index = write++;
      code = 1U;
    }
    else
    {
      encoded[write++] = value;
      ++code;
      if (code == 0xFFU)
      {
        encoded[code_index] = code;
        code_index = write++;
        code = 1U;
      }
    }
  }
  encoded[code_index] = code;
  encoded[write++] = 0U;

  return UartQueue(encoded, write);
}

static void StartSession(uint16_t session)
{
  /* New TCP client: no old status, EVENT or authorization is inherited. */
  ClearUartTx();
  ResetFrameRx();
  Handwheel_TransportDisconnected();
  active_session = session;
  Handwheel_TransportConnected();
}

static void EndSession(uint16_t session)
{
  if ((session != 0U) && (session == active_session)) FailBridge();
}

static void HandleBridgeFrame(const uint8_t *encoded, uint16_t encoded_length)
{
  uint8_t raw[ESP_BRIDGE_RAW_MAX];
  uint16_t raw_length = 0U;
  uint16_t payload_length;
  uint16_t session;
  uint16_t received_crc;
  uint8_t type;

  if (!CobsDecode(encoded, encoded_length, raw, &raw_length) || (raw_length < 8U))
  {
    FailBridge();
    return;
  }

  payload_length = (uint16_t)raw[4] | ((uint16_t)raw[5] << 8);
  if ((raw[0] != ESP_BRIDGE_VERSION) ||
      (payload_length > ESP_BRIDGE_MAX_PAYLOAD) ||
      (raw_length != (uint16_t)(8U + payload_length)))
  {
    FailBridge();
    return;
  }

  received_crc = (uint16_t)raw[6U + payload_length] |
                 ((uint16_t)raw[7U + payload_length] << 8);
  if (received_crc != Crc16Ccitt(raw, (uint16_t)(6U + payload_length)))
  {
    FailBridge();
    return;
  }

  type = raw[1];
  session = (uint16_t)raw[2] | ((uint16_t)raw[3] << 8);

  if (type == ESP_FRAME_TCP_CONNECTED)
  {
    if ((session == 0U) || (payload_length != 0U)) FailBridge();
    else StartSession(session);
  }
  else if (type == ESP_FRAME_TCP_DATA)
  {
    if ((session == 0U) || (session != active_session)) return;
    Handwheel_TransportReceive(&raw[6], payload_length);
  }
  else if (type == ESP_FRAME_TCP_DISCONNECTED)
  {
    if (payload_length != 0U) FailBridge();
    else EndSession(session);
  }
  else if (type == ESP_FRAME_LINK_FAULT)
  {
    /* BOOT has SESSION=0. Any bridge fault is fail-closed, even if an old
     * fault was buffered near a reconnect boundary. */
    if (payload_length != 1U) FailBridge();
    else FailBridge();
  }
  else
    FailBridge();
}

static void HandleIncomingByte(uint8_t value)
{
  if (value == 0U)
  {
    if (encoded_rx_length != 0U)
    {
      HandleBridgeFrame(encoded_rx, encoded_rx_length);
      ResetFrameRx();
    }
    return;
  }

  if (encoded_rx_length == 0U) partial_frame_started_at = HAL_GetTick();
  if (encoded_rx_length >= ESP_BRIDGE_COBS_MAX)
  {
    FailBridge();
    return;
  }
  encoded_rx[encoded_rx_length++] = value;
}
