#include <Arduino.h>
#include <Wire.h>
#include <math.h>

#define I2C_SCL 5
#define I2C_SDA 4

const int MPU_ADDR = 0x68;

const float ACCEL_SENSITIVITY = 2048.0; 
const float GYRO_SENSITIVITY = 16.4;    

int16_t AcX_raw, AcY_raw, AcZ_raw, Tmp_raw, GyX_raw, GyY_raw, GyZ_raw;

int16_t AcX_offset = -372;
int16_t AcY_offset = 14;
int16_t AcZ_offset = 12;   
int16_t GyX_offset = -42;
int16_t GyY_offset = -8;
int16_t GyZ_offset = -1;

const float treshold_fall = 0.52;
const float treshold_impact = 3.66;
const float treshold_gravity = 0.10;

bool start_fall = 0;
bool impact_check = 0;
bool flag_fall = 0;

unsigned long inactive_time = 0;
unsigned long fall_time = 0;
const unsigned long fallTimeout = 3000;
const unsigned long inactivity_time = 1500;

void setup() {

  Serial.begin(115200);
  Wire.begin(I2C_SDA,I2C_SCL);
  
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(0x6B);
  Wire.write(0);
  Wire.endTransmission(true);

  Wire.beginTransmission(MPU_ADDR);
  Wire.write(0x1B);
  Wire.write(0x18);
  Wire.endTransmission(true);

  Wire.beginTransmission(MPU_ADDR);
  Wire.write(0x1C);
  Wire.write(0x18);
  Wire.endTransmission(true);
}

void loop() {

  Wire.beginTransmission(MPU_ADDR);
  Wire.write(0x3B);
  Wire.endTransmission(false);
  Wire.requestFrom(MPU_ADDR, 14, true);
  
  AcX_raw = Wire.read() << 8 | Wire.read(); 
  AcY_raw = Wire.read() << 8 | Wire.read(); 
  AcZ_raw = Wire.read() << 8 | Wire.read(); 
  Tmp_raw = Wire.read() << 8 | Wire.read(); 
  GyX_raw = Wire.read() << 8 | Wire.read(); 
  GyY_raw = Wire.read() << 8 | Wire.read(); 
  GyZ_raw = Wire.read() << 8 | Wire.read();
  
  int16_t AcX_cal = AcX_raw - AcX_offset;
  int16_t AcY_cal = AcY_raw - AcY_offset;
  int16_t AcZ_cal = AcZ_raw - AcZ_offset;
  int16_t GyX_cal = GyX_raw - GyX_offset;
  int16_t GyY_cal = GyY_raw - GyY_offset;
  int16_t GyZ_cal = GyZ_raw - GyZ_offset;

  float ax = ((float)AcX_cal / ACCEL_SENSITIVITY);
  float ay = ((float)AcY_cal / ACCEL_SENSITIVITY);
  float az = ((float)AcZ_cal / ACCEL_SENSITIVITY);     

  float gx = (float)GyX_cal / GYRO_SENSITIVITY;
  float gy = (float)GyY_cal / GYRO_SENSITIVITY;
  float gz = (float)GyZ_cal / GYRO_SENSITIVITY;
  
  float mag_a = sqrt( pow(ax,2) + pow(ay,2) + pow(az,2) );

  if ( mag_a <= treshold_fall ){
    if(!start_fall){
      start_fall = true;
      fall_time = millis();
    }
  }

  if ( start_fall ){
    if( millis() - fall_time >= fallTimeout){
      start_fall = false;
    } else if( mag_a >= treshold_impact){
      
      impact_check = true;
      inactive_time = millis();
      start_fall = false;

    } 
  }

  if ( impact_check ){
    if ( millis() - inactive_time >= inactivity_time && (1 + treshold_gravity >= abs(az) && abs(az) >= 1 - treshold_gravity || 1 + treshold_gravity >= abs(ax) && abs(ax) >= 1 - treshold_gravity )) {
      impact_check = false;
      flag_fall = true;
    }
  }

}

