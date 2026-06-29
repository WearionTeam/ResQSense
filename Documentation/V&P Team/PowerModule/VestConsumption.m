%% VEST CONSUMPTION FILE
clc
clear

%% Configurations
% All Values were adjusted to using T_cycle = 10s, but if needed we
% increase these value (changing some parameters after)
T_cycle = 10; % sec

% ESP
ESP.freq = 80e6; % 80 MHz
ESP.I_active = 56.3e-3; % Para 80MHz
ESP.I_light_sleep = 240e-6; % 240uA
ESP.T_startup = 2e-3; % 2ms

% BUS
bus.I2C_freq = 400e3;
bus.I2C_Vcc = 3.3;
bus.I2C_R_pullup = 10e3;
bus.SPI_freq = 10e6; % Verificar a freq
bus.UART_baudrate = 115200; % Se não der esta, vamos usar 9600

% Modules / Sensors
sensors(1) = struct( ...
    'Name', 'MPU6050', ...
    'bus', 'I2C' , ...
    'fs', 100, ...
    'bytes', 1020, ... % FIFO - 87 amostras (Acc + Gyro: XYZ)
    'FIFO_samples', 85,...
    'I_active', 3.8e-3, 'I_sleep', 10e-6, ... % 3.8 without DMP
    'T_wake', 10e-3, 'T_conversion', 0, ...
    'Vcc', 3.3, ...
    'always_active', true);

sensors(2) = struct( ... % Configurar para ele desligar quando queremos
    'Name', 'DFR1103', ...
    'bus', 'UART' , ... % Ou UART (mas ainda estamos com problemas)
    'fs', 0.1, ...
    'bytes', 80, ...
    'FIFO_samples', 1,...
    'I_active', 46e-3, 'I_sleep', 1e-3, ...
    'T_wake', 2, 'T_conversion', 7,... 
    'Vcc', 3.3, ... 
    'always_active', true); 

sensors(3) = struct( ... % Trabalhar com o modo 'One Shot'
    'Name', 'TMP117', ...
    'bus', 'I2C' , ...
    'fs', 0.1, ... 
    'bytes', 2, ...
    'FIFO_samples', 1,...
    'I_active', 220e-6, 'I_sleep', 5e-6, ...
    'T_wake', 1.25e-3, 'T_conversion', 15.5e-3, ...
    'Vcc', 3.3, ... 
    'always_active', true); 

sensors(4) = struct( ...
    'Name', 'SEN0344', ...
    'bus', 'I2C' , ... 
    'fs', 100, ...
    'bytes', 192, ... % IR + R LED - 3bytes each
    'FIFO_samples', 32,...
    'I_active', 5e-3, 'I_sleep', 7e-6, ...
    'T_wake', 1e-3, 'T_conversion', 0, ...
    'Vcc', 3.3, ...
    'always_active', true);

module_lora = struct( ...
    'Name', 'LoRa', ...
    'SF', 7, ...
    'Bandwith', 125e3, ...
    'Payload_Delta', 7, ... % bytes
    'Payload_Completa', 15, ...
    'Interval', T_cycle, ... % seconds
    'I_active_tx', 120e-3, 'I_active_rx', 10e-3, ...
    'I_sleep', 200e-9, ...
    'Vcc', 3.3, ...
    'T_rx_window', 100e-3, ... % Window for receiving packets
    'T_sleep_scan', 4, 'T_rx_scan', 1); 

module_uwb = struct( ...
    'Name', 'DWM3000', ...
    'Interval', 10, ...
    'I_active_tx', 150e-3, 'I_active_rx', 120e-3,...
    'I_sleep', 10e-6, ...
    'Vcc', 3.3, ...
    'T_twr', 4e-3, ...         % ~4ms por troca TWR
    'Max_anchors', 4, ...      % Procura até 4 coletes âncora
    'T_anchor_timeout',80e-3); % Timeout rigoroso de escuta (ex: 80ms)


%% Sensor Consumption Calculations (Imean)
T_comms_sensor = zeros(1, length(sensors)); 
T_esp_awake_sensor= T_comms_sensor; 
for k = 1:length(sensors)
    if strcmp(sensors(k).bus, 'I2C')
        T_comms_sensor(k) = ((sensors(k).bytes * (8+1)) + 20) / bus.I2C_freq;
    elseif strcmp(sensors(k).bus, 'UART')
        T_comms_sensor(k) = (sensors(k).bytes * (8+2)) / bus.UART_baudrate;
    end
    
    T_esp_awake_sensor(k) = T_comms_sensor(1, k) * ((T_cycle * sensors(k).fs) / sensors(k).FIFO_samples);
    
    if (sensors(k).always_active)
        Q_sensor(k) = sensors(k).I_active * T_cycle; 
    else
        T_active_sensor = (sensors(k).T_wake + sensors(k).T_conversion + T_comms_sensor(k)) * (T_cycle * sensors(k).fs);
        Q_sensor(k) = (sensors(k).I_active * T_active_sensor) + (sensors(k).I_sleep * (T_cycle - T_active_sensor));
    end
end
Q_sensor(3) = Q_sensor(3) * 3; 
T_esp_awake_sensor(3) = T_esp_awake_sensor(3) * 3;


%% Bus Consumption Calculations (Imean)
I_I2C_pullup = bus.I2C_Vcc/bus.I2C_R_pullup * 0.5 * 2;
Q_bus_I2C = I_I2C_pullup * sum(T_esp_awake_sensor);
Q_bus = Q_bus_I2C; 


%% LoRa Consumption Calculations (Imean)
T_sym = (2^module_lora.SF) / module_lora.Bandwith;
calc_sym = @(bytes) 8 + 4.25 + ceil((8 * bytes - 4 * module_lora.SF + 28 + 16) / (4 * module_lora.SF)) * 5;

ToA_Delta = T_sym * calc_sym(module_lora.Payload_Delta);
ToA_Completa = T_sym * calc_sym(module_lora.Payload_Completa);

% --- ADIÇÃO: Trama de Controlo / Alertas ---
% O Pior Caso realista recorrente: 1 Alerta Biométrico (4 Bytes) por minuto.
Payload_Controlo = 4; 
ToA_Controlo = T_sym * calc_sym(Payload_Controlo);
Num_Controlo_per_60s = 1; % Assume-se 1 evento crítico/controlo a cada minuto

Num_Delta_per_60s = 5;
Num_Completa_per_60s = 1;
Retry_Fator = 1.75;  

% -- Carga num Ciclo de 60s --
% 1. Tramas Delta (Tx + Rx Window)
Custo_Tx_Delta = module_lora.I_active_tx * ToA_Delta * Num_Delta_per_60s;
Custo_Rx_Delta = module_lora.I_active_rx * module_lora.T_rx_window * Num_Delta_per_60s;
Tempo_Ativo_Delta = (ToA_Delta + module_lora.T_rx_window) * Num_Delta_per_60s;

% 2. Trama Completa c/ Retries (Tx + Rx Window)
Custo_Tx_Comp = module_lora.I_active_tx * ToA_Completa * Num_Completa_per_60s * Retry_Fator;
Custo_Rx_Comp = module_lora.I_active_rx * module_lora.T_rx_window * Num_Completa_per_60s * Retry_Fator;
Tempo_Ativo_Comp = (ToA_Completa + module_lora.T_rx_window) * Num_Completa_per_60s * Retry_Fator;

% 3. Trama de Controlo/Alerta (Tx + Rx Window à espera de ACK do Tablet)
Custo_Tx_Ctrl = module_lora.I_active_tx * ToA_Controlo * Num_Controlo_per_60s;
Custo_Rx_Ctrl = module_lora.I_active_rx * module_lora.T_rx_window * Num_Controlo_per_60s;
Tempo_Ativo_Ctrl = (ToA_Controlo + module_lora.T_rx_window) * Num_Controlo_per_60s;

% Soma de tudo o que acontece em 60s
Q_lora_60s_active = Custo_Tx_Delta + Custo_Rx_Delta + Custo_Tx_Comp + Custo_Rx_Comp + Custo_Tx_Ctrl + Custo_Rx_Ctrl;
Tempo_Ativo_LoRa_60s = Tempo_Ativo_Delta + Tempo_Ativo_Comp + Tempo_Ativo_Ctrl;
Q_lora_60s_sleep = module_lora.I_sleep * (60 - Tempo_Ativo_LoRa_60s);
Q_lora_60s_total = Q_lora_60s_active + Q_lora_60s_sleep;

Q_lora = Q_lora_60s_total / (60 / T_cycle);

% Atualização do tempo para a ESP32
Tempo_Ativo_LoRa_Total = Tempo_Ativo_LoRa_60s / (60 / T_cycle); 

% Atualização do Duty Cycle Legal (Só Tx Time-on-Air, a Rx Window não conta)
ToA_Total_60s = (ToA_Delta * Num_Delta_per_60s) + ...
                (ToA_Completa * Num_Completa_per_60s * Retry_Fator) + ...
                (ToA_Controlo * Num_Controlo_per_60s); 

LoRa_Duty_cycle = (ToA_Total_60s / 60) * 100; 

% 4. Vest Disconnected (Just to know)
T_scan_cycle = module_lora.T_sleep_scan + module_lora.T_rx_scan; 
Q_lora_scan_cycle = (module_lora.I_active_rx * module_lora.T_rx_scan) + ...
                    (module_lora.I_sleep * module_lora.T_sleep_scan);
I_avg_lora_scan = Q_lora_scan_cycle / T_scan_cycle;

%% UWB Consumption Calculations (Imean)

I_uwb_avg = (module_uwb.I_active_tx + module_uwb.I_active_rx) / 2;

% Cenário A: Colete Cego (Emissão P2P)
% Inicia polling sequencial a 4 ancoras (4ms cada)
T_uwb_cego_ativo = module_uwb.Max_anchors * module_uwb.T_twr; 
Q_uwb_cego = (I_uwb_avg * T_uwb_cego_ativo) + ...
             (module_uwb.I_sleep * (T_cycle - T_uwb_cego_ativo));

% Cenário B: Colete Âncora (Escuta) -> O NOSSO PIOR CASO
% Fica em RX à espera de ser pingado até dar timeout (80ms)
T_uwb_ancora_ativo = module_uwb.T_anchor_timeout;
Q_uwb_ancora = (module_uwb.I_active_rx * T_uwb_ancora_ativo) + ...
               (module_uwb.I_sleep * (T_cycle - T_uwb_ancora_ativo));

% Seleção do Pior Caso para o dimensionamento da Bateria e ESP32
Q_uwb = max(Q_uwb_cego, Q_uwb_ancora);
T_uwb_active_total = max(T_uwb_cego_ativo, T_uwb_ancora_ativo);


%% ESP32 Consumption Calculations (Imean)
T_processing = 0.050; 
Tempo_active_comms = Tempo_Ativo_LoRa_Total + T_uwb_active_total;
T_esp_active_total = sum(T_esp_awake_sensor) + Tempo_active_comms + T_processing;
T_esp_sleep_total = T_cycle - T_esp_active_total;
Q_ESP = (ESP.I_active * T_esp_active_total) + (ESP.I_light_sleep * T_esp_sleep_total);

%% Battery Capacity Calculations
Q_total_vest = Q_ESP + sum(Q_sensor) + Q_bus + Q_lora + Q_uwb;
I_average = Q_total_vest / T_cycle;
Battery_Capacity_Necessary = I_average * 8 * 10^3;
Bateria_com_Fator_Cagaco = Battery_Capacity_Necessary * 1.5 * 1.5;

%% Show Resulsts and Graphs
disp("--------- Results ---------");
disp(" ");
disp("Average Current Consumed: " + I_average*1e3 + "mA");
disp("Time On Air LoRa Delta Message: " + ToA_Delta*1000 + "ms");
% [CORREÇÃO 3]: Estava a imprimir o ToA_Delta outra vez
disp("Time On Air LoRa Complete Message: " + ToA_Completa*1000 + "ms");
disp("Duty Cycle LoRa: " + LoRa_Duty_cycle + "%");
disp(" ");
disp("Battery's Capacity for 8h:");
disp(" > Theorical: " + Battery_Capacity_Necessary + "mAh");
disp(" > Real (whith x1.5): " + Battery_Capacity_Necessary*1.5 + "mAh");
disp(" > Recommended (considering errors in calculations): " + Bateria_com_Fator_Cagaco + "mAh");
disp("So,");
disp("1s Cell Battery (3.7v) = [" + ceil(Battery_Capacity_Necessary*1.5) + ...
    ", " + ceil(Bateria_com_Fator_Cagaco) + "] mAh")
disp("2s Cell Battery (3.7v) = [" + ceil(Battery_Capacity_Necessary*1.5/2) + ...
    ", " + ceil(Bateria_com_Fator_Cagaco/2) + "] mAh")

figure();
dados = [Q_ESP, Q_bus, Q_sensor, Q_lora, Q_uwb] / Q_total_vest * 100;
bar(dados);
xticklabels(['ESP32', 'Bus', {sensors.Name}, module_lora.Name, module_uwb.Name]);
grid on;
xlabel('Components'); ylabel('Percentage of Total Energy Consumption (%)');
title('Energy Consumption Distribution');

%% PEAK CURRENT ANALYSIS
I_peak_sensors = 0;
for k = 1:length(sensors)
    if strcmp(sensors(k).Name, 'TMP117')
        I_peak_sensors = I_peak_sensors + (sensors(k).I_active * 3); % 3x TMP
    else
        I_peak_sensors = I_peak_sensors + sensors(k).I_active;
    end
end
I_peak_LoRa = module_lora.I_active_tx;
I_peak_UWB = module_uwb.I_active_tx;
I_peak_ESP = 100e-3;
I_Max_total = I_peak_sensors + I_peak_LoRa + I_peak_UWB + I_peak_ESP;

% Show Resulsts and Graphs
Safety_Margin = 1.5;
Regulator_Min_Current = I_Max_total * Safety_Margin;
disp(" ");
disp("Peak Current Consumed: " + I_Max_total*1e3 + "mA");
disp("Regulator Requirement: Must be > " + ceil(Regulator_Min_Current*1000) + "mA")