// INICIATOR - MODO 1 IDLE + MODO 2 CICLO UNICO COM INTERSECAO GPS

#include <SPI.h>
#include <math.h>
#include "dw3000.h"

// ---------------- PINOS DWM3000 / ESP32-S3 ----------------
#define PIN_CS    5
#define PIN_MOSI  6
#define PIN_MISO  4
#define PIN_CLK   2
#define PIN_RST   8
#define PIN_IRQ   7
#define PIN_WAKE  9

// Variaveis externas da biblioteca dw3000
extern uint8_t _ss;
extern uint8_t _rst;
extern uint8_t _irq;

// ---------------- CONFIGURACAO UWB ----------------
static dwt_config_t config = {
    5,                  // Canal
    DWT_PLEN_128,       // Preamble length
    DWT_PAC8,           // Preamble acquisition chunk size
    9,                  // TX preamble code
    9,                  // RX preamble code
    1,                  // non-standard 8 symbol
    DWT_BR_6M8,         // Data rate
    DWT_PHRMODE_STD,    // PHY header mode
    DWT_PHRRATE_STD,    // PHY header rate
    (129 + 8 - 8),      // SFD timeout
    DWT_STS_MODE_OFF,   // STS off
    DWT_STS_LEN_64,     // STS length
    DWT_PDOA_M0         // PDOA off
};

#define TX_ANT_DLY 16385
#define RX_ANT_DLY 16385

// Mensagens UWB
// Bytes 5 e 6 indicam o destino: R1 ou R2
// Bytes 7 e 8 indicam o initiator: IN
static uint8_t tx_poll_msg[] = {0x41, 0x88, 0, 0xCA, 0xDE, 'X', 'X', 'I', 'N', 0xE0, 0, 0};

// Bytes 5 e 6 indicam o destino da resposta: IN
// Bytes 7 e 8 indicam quem respondeu: R1 ou R2
static uint8_t rx_resp_msg[] = {0x41, 0x88, 0, 0xCA, 0xDE, 'I', 'N', 'X', 'X', 0xE1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0};

#define ALL_MSG_COMMON_LEN 10
#define ALL_MSG_SN_IDX 2
#define RESP_MSG_POLL_RX_TS_IDX 10
#define RESP_MSG_RESP_TX_TS_IDX 14
#define RESP_MSG_TS_LEN 4

static uint8_t frame_seq_nb = 0;

#define RX_BUF_LEN 20
static uint8_t rx_buffer[RX_BUF_LEN];
static uint32_t status_reg = 0;

#define POLL_TX_TO_RESP_RX_DLY_UUS 2000
#define RESP_RX_TIMEOUT_UUS 1000

static double tof;
static double distance;

extern dwt_txconfig_t txconfig_options;

// ---------------- COORDENADAS GPS FIXAS DOS COLETES ----------------
// O initiator ja conhece previamente a posicao GPS dos dois coletes/responders.
// ATENCAO: estas coordenadas de exemplo deixam R1 e R2 separados cerca de 33.7 m.
// Para os circulos se cruzarem, r1 + r2 tem de ser >= distancia entre R1 e R2.
// --- COORDENADAS GPS DOS RECETORES (Exatamente 1 metro de distância para ESTE) ---
const double R1_LAT = 40.63140049638333;
const double R1_LON = -8.659085593968872;

const double R2_LAT = 40.63140049638333;   // Mantém-se na mesma linha
const double R2_LON = -8.659073757607147;  // Deslocado exatamente 1 metro para a direita (Este)
// Distancias medidas por UWB entre o initiator e cada colete
// r1 = raio do circulo centrado em R1
// r2 = raio do circulo centrado em R2
double r1 = 0.0;
double r2 = 0.0;
bool tem_r1 = false;
bool tem_r2 = false;

// Conversao local aproximada GPS <-> metros
const double METROS_POR_GRAU_LAT = 111132.0;

// Pequena tolerancia para erros numericos, em metros
const double EPS_INTERSECAO_M = 0.001;

// ---------------- MODOS ----------------
enum ModoOperacao {
  MODO_SLEEP = 1,   // idle logico, sem sleep() e sem deepSleep()
  MODO_RANGING = 2  // mede 1 valor R1 + 1 valor R2, calcula e volta ao modo 1
};

ModoOperacao modoAtual = MODO_SLEEP;
bool target_is_R1 = true;

// ---------------- FUNCOES AUXILIARES ----------------
void mostrarMenu() {
  Serial.println("Comandos disponiveis:");
  Serial.println("  1 -> Modo 1: idle logico, fica parado a espera");
  Serial.println("  2 -> Modo 2: medir 1 vez R1, 1 vez R2, calcular os 2 pontos GPS e voltar ao modo 1");
}

void iniciarCicloUnicoModo2() {
  r1 = 0.0;
  r2 = 0.0;
  tem_r1 = false;
  tem_r2 = false;
  target_is_R1 = true;

  modoAtual = MODO_RANGING;

  Serial.println("\nModo 2 ativo: ciclo unico iniciado.");
  Serial.println("Vou medir 1 valor valido de R1 e 1 valor valido de R2.");
  Serial.println("A medir Colete 1 / R1...");
}

void lerComandosSerial() {
  while (Serial.available() > 0) {
    char comando = Serial.read();

    switch (comando) {
      case '1':
        modoAtual = MODO_SLEEP;
        Serial.println("Modo 1 ativo: idle logico. A aguardar comando 2...");
        break;

      case '2':
        iniciarCicloUnicoModo2();
        break;

      case '\n':
      case '\r':
      case ' ':
        break;

      default:
        Serial.println("Comando invalido. Use 1 ou 2.");
        mostrarMenu();
        break;
    }
  }
}

// Esta funcao faz exatamente a intersecao de dois circulos:
// Circulo 1: centro R1, raio r1
// Circulo 2: centro R2, raio r2
// Resultado: dois pontos GPS possiveis, A e B, quando existe intersecao.
bool calcularEImprimirIntersecaoGPS() {
  if (!tem_r1 || !tem_r2) {
    return false;
  }

  // -------------------------------------------------------------------------
  // 1) Converter R1 e R2 de GPS para um plano local em metros
  //    R1 fica na origem: R1 = (0, 0)
  //    R2 fica em:        R2 = (x2, y2)
  // -------------------------------------------------------------------------
  double r1_lat_rad = R1_LAT * PI / 180.0;
  double metros_por_grau_lon = 111320.0 * cos(r1_lat_rad);

  double x1 = 0.0;
  double y1 = 0.0;

  double x2 = (R2_LON - R1_LON) * metros_por_grau_lon;   // Este/Oeste
  double y2 = (R2_LAT - R1_LAT) * METROS_POR_GRAU_LAT;   // Norte/Sul

  double d = sqrt((x2 - x1) * (x2 - x1) + (y2 - y1) * (y2 - y1));

  Serial.println("\n==================================================");
  Serial.println("INTERSECAO DOS DOIS RAIOS GPS/UWB");
  Serial.println("--------------------------------------------------");
  Serial.printf("Centro R1 GPS: %10.6f , %10.6f\n", R1_LAT, R1_LON);
  Serial.printf("Centro R2 GPS: %10.6f , %10.6f\n", R2_LAT, R2_LON);
  Serial.printf("Raio R1 / distancia initiator-R1: %3.3f m\n", r1);
  Serial.printf("Raio R2 / distancia initiator-R2: %3.3f m\n", r2);
  Serial.printf("Distancia entre centros R1-R2:    %3.3f m\n", d);
  Serial.println("--------------------------------------------------");

  if (d <= EPS_INTERSECAO_M) {
    Serial.println("Erro: R1 e R2 estao praticamente no mesmo ponto GPS.");
    Serial.println("Nao e possivel obter dois pontos por intersecao de circulos.");
    Serial.println("==================================================\n");
    return true;
  }

  // -------------------------------------------------------------------------
  // 2) Verificar se os dois circulos se cruzam
  // -------------------------------------------------------------------------
  if (d > (r1 + r2 + EPS_INTERSECAO_M)) {
    Serial.println("NAO HA INTERSECAO.");
    Serial.println("Motivo: os circulos estao separados.");
    Serial.printf("Falta distancia para tocar: %3.3f m\n", d - (r1 + r2));
    Serial.println("Regra necessaria: r1 + r2 >= distancia_R1_R2");
    Serial.println("Com estes valores, nao existem coordenadas GPS reais para os dois pontos.");
    Serial.println("==================================================\n");
    return true;
  }

  if (d < (fabs(r1 - r2) - EPS_INTERSECAO_M)) {
    Serial.println("NAO HA INTERSECAO.");
    Serial.println("Motivo: um circulo esta dentro do outro sem se cruzarem.");
    Serial.printf("Diferenca dos raios: %3.3f m\n", fabs(r1 - r2));
    Serial.println("Regra necessaria: |r1 - r2| <= distancia_R1_R2");
    Serial.println("Com estes valores, nao existem coordenadas GPS reais para os dois pontos.");
    Serial.println("==================================================\n");
    return true;
  }

  // -------------------------------------------------------------------------
  // 3) Calcular a intersecao dos dois circulos em metros
  // -------------------------------------------------------------------------
  double a = ((r1 * r1) - (r2 * r2) + (d * d)) / (2.0 * d);

  double h2 = (r1 * r1) - (a * a);
  if (h2 < 0.0 && h2 > -EPS_INTERSECAO_M) {
    h2 = 0.0;
  }

  if (h2 < 0.0) {
    Serial.println("Erro numerico: h^2 ficou negativo. As medidas provavelmente sao incoerentes.");
    Serial.println("==================================================\n");
    return true;
  }

  double h = sqrt(h2);

  // Vetor unitario de R1 para R2
  double ex = (x2 - x1) / d;
  double ey = (y2 - y1) / d;

  // Ponto base na linha R1->R2
  double px = x1 + a * ex;
  double py = y1 + a * ey;

  // Vetor perpendicular
  double perp_x = -ey;
  double perp_y = ex;

  // Dois pontos de intersecao em metros locais
  double pontoA_este  = px + h * perp_x;
  double pontoA_norte = py + h * perp_y;

  double pontoB_este  = px - h * perp_x;
  double pontoB_norte = py - h * perp_y;

  // -------------------------------------------------------------------------
  // 4) Converter os dois pontos de metros para GPS
  // -------------------------------------------------------------------------
  double pontoA_lat = R1_LAT + (pontoA_norte / METROS_POR_GRAU_LAT);
  double pontoA_lon = R1_LON + (pontoA_este  / metros_por_grau_lon);

  double pontoB_lat = R1_LAT + (pontoB_norte / METROS_POR_GRAU_LAT);
  double pontoB_lon = R1_LON + (pontoB_este  / metros_por_grau_lon);

  // -------------------------------------------------------------------------
  // 5) Imprimir os dois pontos GPS possiveis
  // -------------------------------------------------------------------------
  if (h <= EPS_INTERSECAO_M) {
    Serial.println("Os circulos tocam-se num unico ponto, por isso A e B sao praticamente iguais.");
  } else {
    Serial.println("Os circulos cruzam-se em dois pontos possiveis.");
  }

  Serial.println("--------------------------------------------------");
  Serial.println("COORDENADAS POSSIVEIS DO INITIATOR / TAG:");
  Serial.printf("Ponto A GPS: %10.6f , %10.6f\n", pontoA_lat, pontoA_lon);
  Serial.printf("Ponto B GPS: %10.6f , %10.6f\n", pontoB_lat, pontoB_lon);
  Serial.println("==================================================\n");

  return true;
}

// Mede uma distancia UWB ao alvo atual.
// Se target_is_R1 == true, mede R1.
// Se target_is_R1 == false, mede R2.
bool medirDistanciaUWB() {
  if (target_is_R1) {
    tx_poll_msg[5] = 'R';
    tx_poll_msg[6] = '1';

    rx_resp_msg[7] = 'R';
    rx_resp_msg[8] = '1';
  } else {
    tx_poll_msg[5] = 'R';
    tx_poll_msg[6] = '2';

    rx_resp_msg[7] = 'R';
    rx_resp_msg[8] = '2';
  }

  tx_poll_msg[ALL_MSG_SN_IDX] = frame_seq_nb;

  dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_TXFRS_BIT_MASK);

  dwt_writetxdata(sizeof(tx_poll_msg), tx_poll_msg, 0);
  dwt_writetxfctrl(sizeof(tx_poll_msg), 0, 1);

  dwt_starttx(DWT_START_TX_IMMEDIATE | DWT_RESPONSE_EXPECTED);

  while (!((status_reg = dwt_read32bitreg(SYS_STATUS_ID)) &
           (SYS_STATUS_RXFCG_BIT_MASK | SYS_STATUS_ALL_RX_TO | SYS_STATUS_ALL_RX_ERR))) {
    lerComandosSerial();
    yield();

    if (modoAtual != MODO_RANGING) {
      return false;
    }
  }

  frame_seq_nb++;

  if (status_reg & SYS_STATUS_RXFCG_BIT_MASK) {
    uint32_t frame_len;

    dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_RXFCG_BIT_MASK);

    frame_len = dwt_read32bitreg(RX_FINFO_ID) & RXFLEN_MASK;

    if (frame_len <= sizeof(rx_buffer)) {
      memset(rx_buffer, 0, sizeof(rx_buffer));

      dwt_readrxdata(rx_buffer, frame_len, 0);

      rx_buffer[ALL_MSG_SN_IDX] = 0;

      if (memcmp(rx_buffer, rx_resp_msg, ALL_MSG_COMMON_LEN) == 0) {
        uint32_t poll_tx_ts;
        uint32_t resp_rx_ts;
        uint32_t poll_rx_ts;
        uint32_t resp_tx_ts;

        int32_t rtd_init;
        int32_t rtd_resp;

        float clockOffsetRatio;

        poll_tx_ts = dwt_readtxtimestamplo32();
        resp_rx_ts = dwt_readrxtimestamplo32();

        clockOffsetRatio = ((float)dwt_readclockoffset()) / (uint32_t)(1 << 26);

        resp_msg_get_ts(&rx_buffer[RESP_MSG_POLL_RX_TS_IDX], &poll_rx_ts);
        resp_msg_get_ts(&rx_buffer[RESP_MSG_RESP_TX_TS_IDX], &resp_tx_ts);

        // Compute time of flight and distance, using clock offset ratio to correct
        // for differing local and remote clock rates.
        rtd_init = resp_rx_ts - poll_tx_ts;
        rtd_resp = resp_tx_ts - poll_rx_ts;
        tof = ((rtd_init - rtd_resp * (1 - clockOffsetRatio)) / 2.0) * DWT_TIME_UNITS;
        distance = tof * SPEED_OF_LIGHT;

        if (target_is_R1) {
          r1 = distance;
          tem_r1 = true;
          Serial.print("DISTANCE R1 (meters): ");
        } else {
          r2 = distance;
          tem_r2 = true;
          Serial.print("DISTANCE R2 (meters): ");
        }

        Serial.println(distance);
        return true;
      } else {
        Serial.println("Resposta recebida, mas nao era do colete esperado. Vou tentar novamente.");
      }
    }
  } else {
    dwt_write32bitreg(SYS_STATUS_ID, SYS_STATUS_ALL_RX_TO | SYS_STATUS_ALL_RX_ERR);

    if (target_is_R1) {
      Serial.println("Timeout/erro em R1. A tentar R1 novamente...");
    } else {
      Serial.println("Timeout/erro em R2. A tentar R2 novamente...");
    }
  }

  return false;
}

void executarModo2CicloUnico() {
  bool medicao_valida = medirDistanciaUWB();

  if (!medicao_valida || modoAtual != MODO_RANGING) {
    return;
  }

  // Acabou de medir R1: agora mede R2.
  if (target_is_R1 && tem_r1 && !tem_r2) {
    target_is_R1 = false;
    Serial.println("A medir Colete 2 / R2...");
    return;
  }

  // Ja tem uma medicao R1 e uma medicao R2: calcula a intersecao dos dois raios.
  if (tem_r1 && tem_r2) {
    calcularEImprimirIntersecaoGPS();

    modoAtual = MODO_SLEEP;
    target_is_R1 = true;

    Serial.println("Modo 2 terminado: foi medido 1 valor de R1 e 1 valor de R2.");
    Serial.println("Voltei automaticamente ao Modo 1. Envie 2 para nova medicao.");

    tem_r1 = false;
    tem_r2 = false;
    r1 = 0.0;
    r2 = 0.0;
  }
}

// ---------------- SETUP ----------------
void setup() {
  Serial.begin(115200);
  delay(1000);

  _ss = PIN_CS;
  _rst = PIN_RST;
  _irq = PIN_IRQ;

  SPI.begin(PIN_CLK, PIN_MISO, PIN_MOSI, PIN_CS);

  pinMode(PIN_CS, OUTPUT);
  digitalWrite(PIN_CS, HIGH);

  pinMode(PIN_RST, OUTPUT);
  digitalWrite(PIN_RST, LOW);
  delay(10);
  digitalWrite(PIN_RST, HIGH);
  delay(50);

  while (!dwt_checkidlerc()) {
    Serial.println("ERRO: DW3000 nao entrou em IDLE_RC!");
    while (1) {
      yield();
    }
  }

  Serial.println("DW3000 acordou com sucesso!");

  if (dwt_initialise(DWT_DW_INIT) == DWT_ERROR) {
    Serial.println("Inicializacao Falhada");
    while (1) {
      yield();
    }
  }

  dwt_setleds(DWT_LEDS_ENABLE | DWT_LEDS_INIT_BLINK);

  if (dwt_configure(&config)) {
    Serial.println("Configuracao Falhada");
    while (1) {
      yield();
    }
  }

  dwt_configuretxrf(&txconfig_options);

  dwt_setrxantennadelay(RX_ANT_DLY);
  dwt_settxantennadelay(TX_ANT_DLY);

  dwt_setrxaftertxdelay(POLL_TX_TO_RESP_RX_DLY_UUS);
  dwt_setrxtimeout(RESP_RX_TIMEOUT_UUS);

  dwt_setlnapamode(DWT_LNA_ENABLE | DWT_PA_ENABLE);

  Serial.println("DWM3000 PRONTO! Modo 1 ativo: idle sem sleep/deepsleep.");
  mostrarMenu();
}

// ---------------- LOOP ----------------
void loop() {
  lerComandosSerial();

  switch (modoAtual) {
    case MODO_SLEEP:
      // Modo default:
      // nao mede, nao transmite e nao usa sleep/deepSleep.
      // fica apenas a espera de receber '2' no Serial Monitor.
      yield();
      break;

    case MODO_RANGING:
      // Um ciclo unico: 1 distancia R1 + 1 distancia R2 + intersecao GPS.
      executarModo2CicloUnico();
      break;

    default:
      modoAtual = MODO_SLEEP;
      break;
  }
}
