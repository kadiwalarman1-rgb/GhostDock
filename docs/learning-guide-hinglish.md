# GhostDock: pehle project se engineering tak

Ye guide original Python + Bash + Docker learning ko current implementation se connect karti hai. Repo ko samajhne ke liye pehle simulation chalao, phir code isi order mein padho: `config.py`, `local.py`, `engine.py`, `cloud.py`, `evidence.py`.

## 1. Bash ka `$?` kya batata hai?

`$?` pichhle command ka exit code deta hai. Isliye Docker command ke turant baad save karna zaroori hai. Beech mein `echo` chala diya toh tum Docker ki jagah `echo` ka result padh sakte ho.

`runner.sh` original learning milestone ko preserve karta hai: Docker run karo, return code capture karo, same code parent process ko do. Ye standalone learning utility hai; production-style monitoring `ghostdock/local.py` mein hai.

Code `125` ka matlab Docker ko container run karne mein problem hui. Har `125` running app ka crash nahi hota. Wrong image ko AWS par bhejne se image automatically sahi nahi ho jayegi.

## 2. Python aur Bash ka connection

`subprocess.run()` child command chalata hai. `returncode`, `stdout`, aur `stderr` alag cheezein hain. Error message sirf stdout print karne par chhup sakta hai. `ghostdock/process.py` is boundary ko ek jagah handle karta hai.

Argument list aur `shell=False` ka use kiya gaya hai. Isse command string ko shell syntax ki tarah interpret nahi kiya jata. Phir bhi input validation aur trusted executable environment zaroori hain.

## 3. OOP aur `>>` operator

`LocalNode` Docker ko handle karta hai. `CloudNode` AWS ko. Engine dono ke beech policy decide karta hai.

Original idea ka `__rshift__` ab educational wrapper hai: `local >> (cloud, receipt)`. Actual readable method `local.failover_to(cloud, receipt)` hai. Operator khud migration nahi karta; woh method call ko short syntax deta hai. Tests verify karte hain ki wrapper named method ko delegate karta hai.

## 4. Process running aur app healthy mein difference

Container running ho sakta hai, lekin application HTTP requests ka answer na de. Isliye GhostDock HTTP 200 ke saath expected application marker check karta hai. Marker se pata chalta hai ki response expected app se match karta hai.

Initial deployment healthy hone ke baad monitor arm hota hai. Teen consecutive failed probes default threshold hain. Ek healthy probe count reset karta hai, taaki temporary glitch turant cloud server launch na kare.

## 5. Pehle local recovery kyun?

Agar ek local restart app ko healthy kar de, toh cloud capacity ki zaroorat nahi. Engine ek bounded restart try karta hai. Uske baad bhi failure ho aur live cloud explicitly enabled ho, tab AWS adapter use hota hai.

Docker ki restart policies pehle se available hain. Project ka extra learning area hai recovery decision, cloud ownership, verification aur cleanup ko ek incident ke roop mein manage karna.

## 6. EC2 create hona success ka final proof kyun nahi?

EC2 boot hona, Docker install hona, image pull hona, container start hona aur HTTP app healthy hona alag steps hain. GhostDock SSM ke through cloud instance par HTTP health probe execute karta hai.

Is version mein public traffic switch nahi hota. Cloud application recover hone ke baad bhi user requests ko wahan bhejne ka design alag chahiye. Isi liye README zero downtime claim nahi karta.

## 7. Idempotency aur incident ID

Maan lo AWS ne server create kar diya, lekin response network mein kho gaya. Dobara blindly launch karoge toh extra servers ban sakte hain. Incident UUID ko client token, tags aur local receipt mein use kiya gaya hai.

Ek workload ka local lock aur active-instance check extra protection dete hain. Ye distributed lock nahi hai; alag machines se controllers chalane par proper shared coordination chahiye.

## 8. Cleanup bhi recovery ka part hai

Cloud app healthy na ho toh created instance ko terminate karne ki koshish hoti hai. Permission na ho ya result uncertain ho toh `CLEANUP_REQUIRED` dikhega. False success print karne se bill aur operational confusion dono badh sakte hain.

Successful cloud recovery ke baad instance inspection ke liye running rehta hai. Receipt-specific cleanup command use karna hota hai. TTL timer backup safeguard hai; guaranteed budget cutoff nahi.

## 9. Recovery receipt kya hai?

`events.jsonl` mein har transition record hota hai. `state.json` latest state rakhta hai. `report.html` same information ko readable form mein dikhata hai. Hash chain se inconsistent edits detect ho sakte hain.

Agar koi poori chain rewrite kar sakta hai, woh nayi valid chain bana sakta hai. Isliye ye digitally signed audit log nahi. Interview mein ye limitation explain kar pana engineering understanding dikhata hai.

## 10. Interview mein project kaise explain karna hai?

“Maine Bash exit codes aur Python OOP se shuru kiya. Review mein samjha ki success message, EC2 launch aur application recovery alag hain. Isliye health verification, local-first recovery, incident ownership, cleanup aur testable adapters add kiye.”

Pehle no-cloud demo dikhao. Phir ek failed scenario chalao aur explain karo ki cloud launch kyun block hua. Real Docker restart test dikhao. Live AWS recovery ka claim tab karo jab uska receipt aur cleanup evidence collect kar liya ho.

First project hone ko chhupane ki zaroorat nahi. Decisions, limitations aur test evidence ko confidently explain karna sabse useful preparation hai.
