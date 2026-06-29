%% DASHBOARD TABLET CONSUMPTION FILE
clc
clear

%% Configurations
% Como o sistema tem comportamentos ao segundo e ao minuto, vamos
% analisar o consumo médio num macro-ciclo de 1 minuto (60s).
T_macro = 60; % sec

% --- POWER SUPPLY ---
V_bat_nominal = 7.4; % Assumindo um pack Li-Ion 2S (Muito comum para Tablets)
Converter_Efficiency = 0.90; % Eficiência dos conversores DC-DC (Buck/Boost)

% --- MICROCOMPUTER (Raspberry Pi 4 / 5) ---
% Uma Rasp a correr Linux, com GUI. Processamento matemático de trilateração 2D (leve).
% Não entra em Sleep.
RPi.Vcc = 5.0;
RPi.I_active = 800e-3; % ~4 Watts de consumo base (média realista para GUI)

% --- DISPLAY (Raspberry Pi SC0880 7" Touchscreen) ---
% Sempre ligado com brilho elevado.
Display.Vcc = 5.0;
Display.I_active = 500e-3; % ~2.5 Watts 

% --- SENSORS & PERIPHERALS ---
% Magnetómetro (Bússola para orientar o Radar na GUI)
Mag.Name = 'QMC5883L';
Mag.Vcc = 3.3;
Mag.I_active = 2e-3; % 2mA a ler continuamente a 10Hz-50Hz

% Altifalante / Buzzer (Avisos de Voz / Alarmes)
Audio.Vcc = 5.0;
Audio.I_active = 400e-3; % ~2 Watts em pico sonoro
Audio.T_active_per_min = 5; % Assumimos 5 segundos de alarme a cada 1 minuto no PIOR caso

% --- LORA MODULE (Olimex 868MHz) ---
module_lora.SF = 7;
module_lora.Bandwith = 125e3;
module_lora.Vcc = 3.3;
module_lora.I_active_tx = 120e-3;
module_lora.I_active_rx = 12e-3;
module_lora.I_sleep = 1e-6;
module_lora.T_rx_window = 100e-3; % 100ms de janela de escuta

% Dados da Rede (Pior Caso Absoluto)
Network.Max_Vests = 31; % 31 nós finais
Network.Slots_per_min = 6; % Cada colete fala a cada 10s (6 vezes por minuto)
Network.Control_Wakeups_per_min = 60; % Tablet acorda o RX a cada 1s no Canal de Controlo

%% --- 1. POWER CALCULATIONS (Em Watts) ---

% 1. Raspberry Pi & Display (Constant Power)
P_RPi = RPi.Vcc * RPi.I_active;
P_Display = Display.Vcc * Display.I_active;

% 2. Magnetómetro (Constant Power)
P_Mag = Mag.Vcc * Mag.I_active;

% 3. Audio / Voz (Average Power ao longo de 60s)
P_Audio_Peak = Audio.Vcc * Audio.I_active;
P_Audio_Avg = P_Audio_Peak * (Audio.T_active_per_min / T_macro);

%% --- 2. LORA CONSUMPTION (O Trabalho do Tablet) ---

% Cálculo do Tempo no Ar (Tx) para Comandos
T_sym = (2^module_lora.SF) / module_lora.Bandwith;
calc_sym = @(bytes) 8 + 4.25 + ceil((8 * bytes - 4 * module_lora.SF + 28 + 16) / (4 * module_lora.SF)) * 5;

Payload_Controlo = 4; % Assumimos pacotes de 4 Bytes (Alertas/Retries)
ToA_Controlo = T_sym * calc_sym(Payload_Controlo);

% A. Escuta do Canal de Dados (TDMA)
% O Tablet abre 100ms para CADA slot de CADA colete num minuto
T_rx_data = Network.Max_Vests * Network.Slots_per_min * module_lora.T_rx_window; 

% B. Escuta do Canal de Controlo/Alertas (CSMA/LBT)
% O Tablet abre 100ms a cada 1 segundo
T_rx_control = Network.Control_Wakeups_per_min * module_lora.T_rx_window;

% C. Transmissão de Comandos (Pior Caso)
% Assumimos que envia 1 comando (Retry/ACK/Sync) a CADA colete num minuto
Num_Tx_Tablet = Network.Max_Vests * 1; 
T_tx_total = ToA_Controlo * Num_Tx_Tablet;
LoRa_Duty_cycle = (T_tx_total / T_macro) * 100;

% D. Tempos e Potência do LoRa
T_lora_active = T_rx_data + T_rx_control + T_tx_total;

% Saturação de Segurança: O rádio não pode estar ativo mais de 60s por minuto
if T_lora_active > T_macro
    T_lora_active = T_macro; % Força a 100% duty cycle ativo
    T_rx_data = T_macro - T_tx_total; % Ajusta o tempo de RX
end

T_lora_sleep = T_macro - T_lora_active;

E_lora_tx = module_lora.I_active_tx * module_lora.Vcc * T_tx_total;
E_lora_rx = module_lora.I_active_rx * module_lora.Vcc * (T_rx_data + T_rx_control);
E_lora_sleep = module_lora.I_sleep * module_lora.Vcc * T_lora_sleep;

P_LoRa_Avg = (E_lora_tx + E_lora_rx + E_lora_sleep) / T_macro;

%% --- 3. TOTAL SYSTEM POWER & BATTERY SIZING ---

% Potência Média Total do Tablet (Watts) Interna
P_Total_Tablet = P_RPi + P_Display + P_Mag + P_Audio_Avg + P_LoRa_Avg;

% Considerando perdas de conversão dos reguladores (Boost/Buck) para a Bateria
P_Total_Real = P_Total_Tablet / Converter_Efficiency; 

% Dimensionamento da Bateria para 8 Horas
Horas_Missao = 8;
Energia_Necessaria_Wh = P_Total_Real * Horas_Missao; % Watt-hora

% Conversão de Watt-hora para mAh (com base na tensão da bateria escolhida)
Capacidade_Bateria_mAh = (Energia_Necessaria_Wh / V_bat_nominal) * 1000;
Bateria_Recomendada_mAh = Capacidade_Bateria_mAh * 1.3; % Margem de segurança (30%)

%% --- 4. OUTPUT RESULTS & GRAPHS ---
fprintf('\n--------- DASHBOARD TABLET RESULTS ---------\n');
fprintf('Raspberry Pi Average Power: %.2f W\n', P_RPi);
fprintf('TouchScreen Average Power:      %.2f W\n', P_Display);
fprintf('Speaker Average Power:        %.3f W\n', P_Audio_Avg);
fprintf('LoRa Average Power:         %.3f W\n', P_LoRa_Avg);
fprintf('--------------------------------------------\n');
fprintf('TOTAL Average Power:      %.2f W\n', P_Total_Real);

%fprintf('\nLoRa Acive (Rx + Tx):  %.1f s por minuto (%.1f%% do tempo)\n', T_lora_active, (T_lora_active/T_macro)*100);
%fprintf('Duty Cycle LoRa (Legal: <1%%):  %.2f %%\n', LoRa_Duty_cycle);

fprintf('\nBattery Capacity for 8 (Pack %.1fV):\n', V_bat_nominal);
fprintf(' > Theorical:     %.0f mAh\n', Capacidade_Bateria_mAh);
fprintf(' > Recomended (+30%%): %.0f mAh\n', Bateria_Recomendada_mAh);



figure('Color','w');

% Calcular percentagens
dados_bar = [P_RPi, P_Display, P_Audio_Avg, P_Mag, P_LoRa_Avg] / P_Total_Tablet * 100;

b = bar(dados_bar, 'FaceColor', 'flat');

% Atribuir cores diferentes a cada componente para ficar apelativo
b.CData(1,:) = [0.8500 0.3250 0.0980]; % RPi (Laranja)
b.CData(2,:) = [0 0.4470 0.7410];      % Ecrã (Azul)
b.CData(3,:) = [0.9290 0.6940 0.1250]; % Audio (Amarelo)
b.CData(4,:) = [0.4940 0.1840 0.5560]; % Mag (Roxo)
b.CData(5,:) = [0.4660 0.6740 0.1880]; % LoRa (Verde)

xticklabels({'Raspberry Pi', 'TouchScreen', 'Speaker', 'Magnetometer', 'LoRa'});
xtickangle(25);
ylabel('Percentage of Total Energy Consumption (%)');
grid on;
title(sprintf('Energy Consumption Distribution'));

% Adicionar as percentagens em cima das barras
%xtips = b.XEndPoints;
%ytips = b.YEndPoints;
%labels = string(round(ytips,2)) + '%';
%text(xtips, ytips, labels, 'HorizontalAlignment','center', 'VerticalAlignment','bottom');

% Para efeitos de teste de stress à rede e dimensionamento de bateria, foi simulado um Worst-Case Scenario onde o
%  Tablet é forçado a intervir e transmitir um comando a todos os 31 nós da rede a cada minuto. Neste cenário extremo
%  e irrealista, o Duty Cycle atingiria os 1.18%, ligeiramente acima do limite legal ETSI de 1%.

% Contudo, como a arquitetura da rede adota o envio de tráfego de telemetria não-confirmado (TDMA passivo),
%  o Tablet apenas emite tramas de controlo em situações de exceção (perda de pacotes ou alarmes). Conclui-se
%  que o Duty Cycle Nominal do Tablet será uma fração diminuta de 1%, garantindo total conformidade legal e margem
%  para escalar a rede no futuro.