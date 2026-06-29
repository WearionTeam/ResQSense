 # ResQSense

**ResQSense** is a smart vest project designed to protect those who give everything for us: firefighters. The system monitors vital signs, detects falls, and tracks the user's location in real time, sending all data over radio using **LoRa** technology (essential for scenarios without internet or mobile network coverage).

## Repository Structure

* **Documentation:** Project reports, test logs, and explanations of how the algorithms work (such as Man-Down and PDR).
* **Management:** Project organization and our Gantt chart template for tracking deadlines.
* **Schematics:** Hardware electrical schematics (native KiCad files and PDF versions for direct viewing on GitHub).
* **firmware:** Contains the C++ code. Includes test examples (UWB, vitals reading, etc.) and the final project divided into two parts: the vest (**vest**) and the central hub (**dashboard-tablet**).
* **shared:** Stores the global configuration files (`config.h` and `protocol.h`) that define the hardware pins and the data packet structure.
    * **Important Note:** This folder is only required for the vest's firmware (**vest**). It was created separately and mapped in the `platformio.ini` file to cleanly share definitions, but it is not used in any other project code (such as the dashboard or tablet).

## Main Vest Features

* **Fall Detection (Man-Down):** The IMU (MPU-6050) detects free falls and impacts. If the user remains immobile, it triggers an SOS.
* **Vital Signs:** Heart rate and blood oxygen (SpO2) measurement, along with a fault-tolerant system featuring 3 temperature sensors (TMP117).
* **Hybrid Navigation:** Uses GPS/GNSS, but if the signal is lost (e.g., inside a pavilion), it uses IMU steps to estimate the position.
* **Custom TDMA LoRa Network:** We created a time-slotted LoRa network (TDMA) to allow multiple vests to communicate with the central hub without packet collisions, featuring AES-128 encryption.

## How to run the Vest Code

The software project was developed using **PlatformIO**. To test or upload the code to the board:

1. Install [Visual Studio Code](https://code.visualstudio.com/) and the **PlatformIO** extension.
2. Clone this repository to your PC:
   ```bash
   git clone [https://github.com/WearionTeam/ResQSense.git](https://github.com/WearionTeam/ResQSense.git)```
3. Change your path by making and start VSCode: 
   ```bash
   cd ResQSense/firmware/vest/ResQSenseVest
   code .```
4. Open the **PlatformIO** terminal and type:
   ```bash
   pio run -t upload -e esp32s3usbotg```
   
## How to run the Dashboard Code

The dashboard is built in **Python**. To run it locally:

1. Ensure you have **Python 3** installed in your Rasp.
2. Navigate to the dashboard directory containing the Python scripts
   ```bash
   cd ResQSense/firmware/dashboard```
3. Run the main dashboard interface:
   ```bash
   python3 testedashboard.py```
