// RECETOR 1/2

#include <SPI.h>
#include "dw3000.h"

#define PIN_CS    5
#define PIN_MOSI  6
#define PIN_MISO  4
#define PIN_CLK   2
#define PIN_RST   8
#define PIN_IRQ   7
#define PIN_WAKE  9

// External variables from dw3000 library
extern uint8_t _ss; 
extern uint8_t _rst; 
extern uint8_t _irq;

// Default communication configuration - non-STS DW mode
static dwt_config_t config = {
    5,                  // Channel number
    DWT_PLEN_128,       // Preamble length - Used in TX only
    DWT_PAC8,           // Preamble acquisition chunk size - Used in RX only
    9,                  // TX preamble code - Used in TX only
    9,                  // RX preamble code - Used in RX only
    1,                  // non-standard 8 symbol
    DWT_BR_6M8,         // Data rate
    DWT_PHRMODE_STD,    // PHY header mode
    DWT_PHRRATE_STD,    // PHY header rate
    (129 + 8 - 8),      // SFD timeout - Used in RX only
    DWT_STS_MODE_OFF,   // STS disabled
    DWT_STS_LEN_64,     // STS length see allowed values in Enum dwt_sts_lengths_e
    DWT_PDOA_M0         // PDOA mode off
};

#define DELAY_MS 1000   // Delay in between raging.

// Antenna Delay for 64 MHz PRF (need to be calibrated)
#define TX_ANT_DLY 16385
#define RX_ANT_DLY 16385

// Raging process frames
static uint8_t rx_poll_msg[] = {0x41, 0x88, 0, 0xCA, 0xDE, 'R', '1', 'I', 'N', 0xE0, 0, 0};                         // 'R', '2', 'I', 'N'
static uint8_t tx_resp_msg[] = {0x41, 0x88, 0, 0xCA, 0xDE, 'I', 'N', 'R', '1', 0xE1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0}; // 'I', 'N', 'R', '1'
#define ALL_MSG_COMMON_LEN 10 // Length of the common part in bytes: from 0x41 to 0xE0

// Indexes to access some of the fields difined above
#define ALL_MSG_SN_IDX 2                  // Access to frame index (0)
#define RESP_MSG_POLL_RX_TS_IDX 10        // Access to MSG in Rx
#define RESP_MSG_RESP_TX_TS_IDX 14        // Access to MSG in Tx
#define RESP_MSG_TS_LEN 4                 // Access to fram of length (0xDE)

static uint8_t frame_seq_nb = 0;          // Frame sequence number (counter)

#define RX_BUF_LEN 12                     // Buffer to store received response message
static uint8_t rx_buffer[RX_BUF_LEN];     // Array with that space

static uint32_t status_reg = 0;           // Status register tracker for debugging

#define POLL_RX_TO_RESP_TX_DLY_UUS 2500    // Delay between frames (in us)

// Timestamps of Tx/Rx frames
static uint64_t poll_rx_ts;         
static uint64_t resp_tx_ts;               

extern dwt_txconfig_t txconfig_options;   // faço puto


void setup() {
  Serial.begin(115200); 
  delay(1000);
  _ss = PIN_CS; 
  _rst = PIN_RST; 
  _irq = PIN_IRQ;
  
  // Configure SPI
  SPI.begin(PIN_CLK, PIN_MISO, PIN_MOSI, PIN_CS);
  pinMode(PIN_CS, OUTPUT); 
  digitalWrite(PIN_CS, HIGH);
  
  pinMode(PIN_RST, OUTPUT); 
  digitalWrite(PIN_RST, LOW); delay(10); 
  digitalWrite(PIN_RST, HIGH); delay(50);

  while (!dwt_checkidlerc()) {
    Serial.println("ERRO: DW3000 não entrou em IDLE_RC!");
    while(1) yield(); // Fica preso aqui em segurança caso haja falha de hardware
  }
  Serial.println("DW3000 acordou com sucesso!");

  if (dwt_initialise(DWT_DW_INIT) == DWT_ERROR) {
    Serial.println("Inicialização Falhada"); 
    while(1) yield();
  }

  dwt_setleds(DWT_LEDS_ENABLE | DWT_LEDS_INIT_BLINK);

  if (dwt_configure(&config)) {
    Serial.println("Configuração Falhada"); 
    while(1) yield();
  }

  dwt_configuretxrf(&txconfig_options); // Configure the TX spectrum parameters
  
  //Apply default antenna delay value
  dwt_setrxantennadelay(RX_ANT_DLY);
  dwt_settxantennadelay(TX_ANT_DLY);

  dwt_setlnapamode(DWT_LNA_ENABLE | DWT_PA_ENABLE);

  Serial.println("DWM3000 PRONTO! A medir...");
}

void loop() {
  //* Activate reception immediately. */
  dwt_rxenable(DWT_START_RX_IMMEDIATE);

  /* Poll for reception of a frame or error/timeout. See NOTE 6 below. */
  while (!((status_reg = dwt_read32bitreg(SYS_STATUS_ID)) & (SYS_STATUS_RXFCG_BIT_MASK | SYS_STATUS_ALL_RX_ERR)))
  { yield();};

  if (status_reg & SYS_STATUS_RXFCG_BIT_MASK)
  {
      uint32_t frame_len;

      // Clear good RX frame event in the DW IC status register
      dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_RXFCG_BIT_MASK);

      // A frame has been received, read it into the local buffer
      frame_len = dwt_read32bitreg(RX_FINFO_ID) & RXFLEN_MASK;
      if (frame_len <= sizeof(rx_buffer))
      {
          dwt_readrxdata(rx_buffer, frame_len, 0);

          /* Check that the frame is a poll sent by "SS TWR initiator" example.
            * As the sequence number field of the frame is not relevant, it is cleared to simplify the validation of the frame. */
          rx_buffer[ALL_MSG_SN_IDX] = 0;
          if (memcmp(rx_buffer, rx_poll_msg, ALL_MSG_COMMON_LEN) == 0)
          {
              uint32_t resp_tx_time;
              int ret;

              poll_rx_ts = get_rx_timestamp_u64(); // Retrieve poll reception timestamp

              // Compute response message transmission time
              resp_tx_time = (poll_rx_ts + (POLL_RX_TO_RESP_TX_DLY_UUS * UUS_TO_DWT_TIME)) >> 8;
              dwt_setdelayedtrxtime(resp_tx_time);

              /* Response TX timestamp is the transmission time we programmed plus the antenna delay. */
              resp_tx_ts = (((uint64_t)(resp_tx_time & 0xFFFFFFFEUL)) << 8) + TX_ANT_DLY;

              // Write all timestamps in the final message
              resp_msg_set_ts(&tx_resp_msg[RESP_MSG_POLL_RX_TS_IDX], poll_rx_ts);
              resp_msg_set_ts(&tx_resp_msg[RESP_MSG_RESP_TX_TS_IDX], resp_tx_ts);

              // Write and send the response message
              tx_resp_msg[ALL_MSG_SN_IDX] = frame_seq_nb;
              dwt_writetxdata(sizeof(tx_resp_msg), tx_resp_msg, 0); // Zero offset in TX buffer
              dwt_writetxfctrl(sizeof(tx_resp_msg), 0, 1);          // Zero offset in TX buffer, ranging
              ret = dwt_starttx(DWT_START_TX_DELAYED);

              // If dwt_starttx() returns an error, abandon this ranging exchange and proceed to the next one
              if (ret == DWT_SUCCESS)
              {
                  // Poll DW IC until TX frame sent event set
                  while (!(dwt_read32bitreg(SYS_STATUS_ID) & SYS_STATUS_TXFRS_BIT_MASK))
                  { yield();};

                  dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_TXFRS_BIT_MASK);  // Clear TXFRS event
                  frame_seq_nb++;   // Increment frame sequence number after transmission
              }
          }
      }
  }
  else
  {
      dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_ALL_RX_ERR);  // Clear RX error events in the DW IC status register
  }
}