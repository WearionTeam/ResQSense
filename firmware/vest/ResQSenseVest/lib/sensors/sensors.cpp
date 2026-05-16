#include "sensor.h"


void startIMU()
{
    Wire.beginTransmission(IMU_ADDR);
    Wire.write(0x6B);
    Wire.write(0);
    Wire.endTransmission(true);
    Wire.beginTransmission(IMU_ADDR);
    Wire.write(0x1B);
    Wire.write(0x18);
    Wire.endTransmission(true);
    Wire.beginTransmission(IMU_ADDR);
    Wire.write(0x1C);
    Wire.write(0x18);
    Wire.endTransmission(true);
}

void startGNSS(DFRobot_GNSSAndRTC_I2C &component)
{
    while(!component.begin())
    {
        delay(500);
    }
    component.setGnss(eGPS_BeiDou_GLONASS);
    component.enablePower();
}

void readIMU(sensors_data &component)
{
    Wire.beginTransmission(IMU_ADDR);
    Wire.write(0x3B);
    Wire.endTransmission(false);
    Wire.requestFrom(IMU_ADDR, 14);

    uint16_t AcX_raw = Wire.read() << 8 | Wire.read(); 
    uint16_t AcY_raw = Wire.read() << 8 | Wire.read(); 
    uint16_t AcZ_raw = Wire.read() << 8 | Wire.read(); 
    uint16_t Tmp_raw = Wire.read() << 8 | Wire.read(); 
    uint16_t GyX_raw = Wire.read() << 8 | Wire.read(); 
    uint16_t GyY_raw = Wire.read() << 8 | Wire.read(); 
    uint16_t GyZ_raw = Wire.read() << 8 | Wire.read();

    int16_t AcX_cal = AcX_raw - IMU_OFFSET_ACCEL_Z;
    int16_t AcY_cal = AcY_raw - IMU_OFFSET_ACCEL_Y;
    int16_t AcZ_cal = AcZ_raw - IMU_OFFSET_ACCEL_Z;
    int16_t GyX_cal = GyX_raw - IMU_OFFSET_GYRO_X;
    int16_t GyY_cal = GyY_raw - IMU_OFFSET_GYRO_Y;
    int16_t GyZ_cal = GyZ_raw - IMU_OFFSET_GYRO_Z;

    component->ax = ((float)AcX_cal / IMU_ACCEL_SENSIVITY);
    component->ay = ((float)AcY_cal / IMU_ACCEL_SENSIVITY);
    component->az = ((float)AcZ_cal / IMU_ACCEL_SENSIVITY);     

    component->gx = (float)GyX_cal / IMU_GYRO_SENSIVITY;
    component->gy = (float)GyY_cal / IMU_GYRO_SENSIVITY;
    component->gz = (float)GyZ_cal / IMU_GYRO_SENSIVITY;

    component->mag_a = sqrt(pow(component->ax,2) + pow(component->ay,2) + pow(component->az,2));
}

void mandow(sensors_data &component)
{
    
}
