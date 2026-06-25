**Tags:**

# Date 28/02/2026
In this day, I did some tests to start tweaking threshold values for ManDown algorithm. Every test for this particular phase is done 10 times, with a time window about 10 seconds. Normally I stand still for about 5 seconds and then do the action. The actions done today were:
- Falling forward and catching me with the hands on the floor to "amortecer" the fall and then I stay still for 2 seconds;
- Sit down we some force.
Each test data was saved in a .csv file with the name "Action(number)Test.csv" (for example: "SitDown1Test.csv"). Then with a script I read every file and plot the acceleration vector magnitude individually.  
## Falling Forward Test Analysis
Representing an example of each graphic and analysing each one of the 10 graphics in the background.
![[Pasted image 20260302004330.png]]
So in the 1st  test I can see a drop down in the magnitude, probably representing the **weightless phenomenon** with the magnitude reaching values of **0.5** . Followed by the **impact** reaching a magnitude equal to **3.26**. Then I remained **still on the ground** for some time, letting the magnitude adjust at **1**, after some time I got up so the tweaks in the magnitude in the last couple seconds.  
![[Pasted image 20260302004352.png]]
The second test represents a higher impact peak of about 4.13 but the 1st time weightless is "detected" the magnitude is equal to 0.57.
By analysing each one of them, most of the falling test represents the weightless phenomenon with a **magnitude having a slope tending to zero** but hitting it's **lowest at around 0.5** . On the other hand, the **peak of impact is between 3.5 and 4**. Even though that is possibly related to the **height of the fall**, as I'm in a little shorter side, about 1.73 . The **peak is bound to change**.
## Sit Down Test Analysis
Representing an example of each graphic and analysing each one of the 10 graphics in the background.
![[Pasted image 20260302130813.png]]
The graphic above is the chosen one to represent the vast majority. By analysing it the weightless phenomenon has a minimum value greater than 0.5 . In the other hand, the peak impact has a magnitude value similar to the forward falling test, where the values are between 3 and 4.5 . 
![[Pasted image 20260302130830.png]]
This graphic represents a peculiar test made, where the magnitude was lowest then the 0.5 minimum threshold previous stated. This is the only particular thing about this graphic/test.
# Date 01/03/2026
## Jump Test Analysis
![[Pasted image 20260302172108.png]]
This graphic represents the absolute majority behaviour of the acceleration vector magnitude in this type of test. This graphic represents two slopes and two peaks. Seeing the human movement when making the jump action, I can state that the 1st slope is derivative of a human gathering strength on his legs to make the jump, the 1st peak is when the accelerometer feels the actual jump action, then as we are in the air the accelerometer doesn't feel nothing and finally we hit the floor, so the accelerometer measures that impact as the highest peak.
After reviewing this type of graphic, I have to add another "layer" in the ManDown algorithm. As the jump action which isn't meant to be detected, has a similar behaviour as falling forward we can't have the acceleration vector magnitude dictate the outcome of the alert alone. When jumping humans have a high probability of landing facing the same direction, even when we don't face the same direction the axis of IMU stays the same. In the other hand, when you fall the axis change. So we can use this as another layer of our algorithm. 

# Date 03/03/2026
## Catch Test Analysis
This test analyses the behaviour of the acceleration magnitude when catching the device or stopping a fall-like motion.
![[Pasted image 20260303230000.png]]
This graphic represents a clean execution of the catch action. The magnitude experiences a sharp "deep" or drop-down, reaching its lowest point at approximately **0.38g**. This value is notably lower than the 0.5g threshold observed in fall tests, representing the rapid weightless phase immediately preceding the catch. Following this dip, the acceleration magnitude surges to a peak of approximately **1.52g** around the 5.5-second mark. This impact peak is significantly lower than the 3.0g to 4.5g range recorded during actual falls or forceful sitting, suggesting that while the "deep" is pronounced, the resulting energy is insufficient to trigger a ManDown alert. The signal then stabilizes smoothly back toward the 1g baseline, confirming a controlled movement.
![[Pasted image 20260303230025.png]]
The graphic for **Catch6.csv** represents a deviation from the rule. The weightless phase is not a single clean drop; it has a jagged, inconsistent shape reaching down to roughly 0.58. The recovery peak is followed by a secondary dip and more significant oscillations before returning to 1g, suggesting a "messier" catch or multiple contact points during the action.
