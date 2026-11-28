import numpy as np, soundfile as sf, os, math, random
SR=44100; rng=np.random.default_rng(51); random.seed(51)
ROOT=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sounds')
def save(vendor, pack, folder, name, y, stereo_w=0.0):
    y=np.asarray(y,float); pk=np.max(np.abs(y))+1e-9; y=y/pk*0.89
    if stereo_w:
        d=int(SR*0.011); r=np.concatenate([np.zeros(d),y[:-d]]); y=np.stack([y*(1-stereo_w*0.3)+r*stereo_w*0.3, y*(1-stereo_w*0.3)+0.0*r],1)
    p=os.path.join(ROOT,vendor,pack,folder); os.makedirs(p,exist_ok=True); sf.write(os.path.join(p,name+'.flac'),y,SR)
t_=lambda d: np.arange(int(SR*d))/SR
def env(d,a,dec): t=t_(d); return np.minimum(1,t/max(a,1e-4))*np.exp(-t/dec)
def noise(d): return rng.standard_normal(int(SR*d))
def lp(x,a): y=np.zeros_like(x); s=0
def onepole(x,c):
    y=np.empty_like(x); s=0.0
    for i,v in enumerate(x): s+=c*(v-s); y[i]=s
    return y
def hp(x,c): return x-onepole(x,c)
def sine(f,d,ph=0): return np.sin(2*np.pi*np.cumsum(np.broadcast_to(f,(int(SR*d),)))/SR+ph)
NOTE={'C':0,'C#':1,'D':2,'Eb':3,'E':4,'F':5,'F#':6,'G':7,'Ab':8,'A':9,'Bb':10,'B':11}
hz=lambda m: 440*2**((m-69)/12)
ROS,DD,A51,MR,MINE='Roswell Ranch Recordings','Dust Devil Audio','Area 51 Instruments','Midnight Rodeo Sound Co','Mine'
# --- Abduction Drums
def kick(i):
    d=0.5+0.05*(i%4); t=t_(d); f=45+i*3+150*np.exp(-t/(0.02+0.004*i)); y=np.sin(2*np.pi*np.cumsum(f)/SR)*np.exp(-t/(0.18+0.02*(i%5)))
    y+= 0.3*np.sin(2*np.pi*(900+300*i)*t)*np.exp(-t/0.004)   # beam click
    if i%3==0: y+=0.15*np.sin(2*np.pi*(1200+80*np.sin(2*np.pi*7*t))*t)*np.exp(-t/0.12)  # saucer ring
    return y
names_k=['Crop Circle','Mothership','Landing Gear','Gravity Well','Cattle Drop','Silo Thump','Probe Boot','Desert Floor','Hangar 18','Tin Foil']
for i,n in enumerate(names_k): save(ROS,'Abduction Drums Vol 1','Kicks',f'Kick - {n}',kick(i))
def snare(i):
    d=0.35; t=t_(d); body=np.sin(2*np.pi*(180+15*i)*t)*np.exp(-t/0.05)
    n=hp(noise(d),0.2+0.05*(i%3))*np.exp(-t/(0.07+0.02*(i%4)))
    ring=0.25*np.sin(2*np.pi*(2400+200*i)*t+3*np.sin(2*np.pi*35*t))*np.exp(-t/0.15) if i%2 else 0
    return body*0.7+n+ring
names_s=['Abduction','Saloon Door','Ray Gun Rim','Barn Siren','Close Encounter','Bounty','Holster Slap','Static Spur','Ion Storm','Sheriff']
for i,n in enumerate(names_s): save(ROS,'Abduction Drums Vol 1','Snares',f'Snare - {n}',snare(i))
def hat(i,open_=False):
    d=0.45 if open_ else 0.09; t=t_(d); x=hp(hp(noise(d),0.6),0.6)
    metal=sum(np.sign(np.sin(2*np.pi*f*t)) for f in [3140+i*40,4160,5870,7210])*0.08
    return (x+metal)*np.exp(-t/(0.15 if open_ else 0.025))
for i in range(8): save(ROS,'Abduction Drums Vol 1','Hats',f'Closed Hat - Antenna {i+1:02d}',hat(i))
for i in range(5): save(ROS,'Abduction Drums Vol 1','Hats',f'Open Hat - Radar Sweep {i+1:02d}',hat(i,True))
def perc(i):
    d=0.3; t=t_(d); f=300+i*170
    return np.sin(2*np.pi*f*t*(1+0.5*np.exp(-t/0.03)))*np.exp(-t/0.06)+0.2*hp(noise(d),0.5)*np.exp(-t/0.01)
for i,n in enumerate(['Cowbell Beacon','Lasso Knock','Bucket','Tin Can','Wagon Wheel','Signal Block','Telegraph']): save(ROS,'Abduction Drums Vol 1','Perc',f'Perc - {n}',perc(i))
# loops: assemble from above
def loop(bpm,bars,pat,seed):
    rr=np.random.default_rng(seed); s16=60/bpm/4; L=int(SR*s16*16*bars); y=np.zeros(L+SR)
    K=kick(seed%10); S=snare(seed%10); H=hat(seed%8); P=perc(seed%7)
    for b in range(bars):
        for k in range(16):
            pos=int(SR*s16*(b*16+k))
            for ch,smp,g in [(pat[0],K,1),(pat[1],S,0.8),(pat[2],H,0.35),(pat[3],P,0.3)]:
                if ch[k]=='x' or (ch[k]=='?' and rr.random()<0.5): y[pos:pos+len(smp)]+=smp[:len(y)-pos]*g
    return y[:L]
pats=[("x.....x...x.....","....x.......x...","x.x.x.x.x.x.x.x.","...x......x....."),
      ("x...x...x...x...","....x.......x...","..x...x...x...x.","x..x..x..x..x..."),
      ("x.....xx..x...?.","....x.......x..?","x?x?x?x?x?x?x?x?","..?....x....?...")]
for i,(bpm,p) in enumerate([(90,0),(92,2),(120,1),(124,1),(86,2),(100,0)]):
    save(ROS,'Tractor Beam Loops',f'{bpm} BPM',f'Tractor Beam Loop {i+1:02d} - {bpm} BPM',loop(bpm,2,pats[p],i+3))
# --- Dust Devil Audio: Spurs and Saddles Foley
def spur(i):
    d=0.8; t=t_(d); y=np.zeros(len(t))
    for j in range(3+i%3):
        st=int(SR*(0.02+j*0.045)); f=[5200,6100,7300,4700][j%4]+i*60
        seg=np.sin(2*np.pi*f*t[:len(t)-st])*np.exp(-t[:len(t)-st]/0.18)+0.5*np.sin(2*np.pi*f*1.51*t[:len(t)-st])*np.exp(-t[:len(t)-st]/0.1)
        y[st:]+=seg*(0.8**j)
    return y
for i in range(8): save(DD,'Spurs and Saddles Foley','Spur Jingles',f'Spur Jingle {i+1:02d}',spur(i))
def whip(i):
    d=0.5; t=t_(d); sw=noise(d)*np.minimum(1,(t/0.18)**3)*np.exp(-np.maximum(0,t-0.18)/0.02)
    crack=hp(noise(d),0.7)*np.exp(-np.abs(t-0.19)/0.003)*4
    return onepole(sw,0.3+0.05*i)+crack
for i in range(6): save(DD,'Spurs and Saddles Foley','Whip Cracks',f'Whip Crack {i+1:02d}',whip(i))
def hoof(i):
    d=1.2; t=t_(d); y=np.zeros(len(t))
    for j,o in enumerate([0,0.12,0.3,0.42,0.6,0.72,0.9]):
        st=int(SR*o); n=len(t)-st; tt=t[:n]
        y[st:]+=(np.sin(2*np.pi*(220+40*(j%2)+i*15)*tt)*np.exp(-tt/0.03)+0.4*onepole(noise(n/SR),0.4)*np.exp(-tt/0.015))*(0.7+0.3*(j%2))
    return y
for i in range(6): save(DD,'Spurs and Saddles Foley','Hoof Clops',f'Hoof Gallop {i+1:02d}',hoof(i))
def creak(i):
    d=1.4; t=t_(d); f=80+30*np.sin(2*np.pi*(0.6+0.1*i)*t)+i*10
    pulses=np.sign(np.sin(2*np.pi*np.cumsum(f)/SR))*np.exp(-((t-0.7)/0.45)**2)
    return onepole(pulses+0.1*noise(d),0.25)
for i in range(5): save(DD,'Spurs and Saddles Foley','Saddle Creaks',f'Saddle Creak {i+1:02d}',creak(i))
def wind(i):
    d=6.0; t=t_(d); x=noise(d); c=0.02+0.015*(1+np.sin(2*np.pi*(0.11+0.03*i)*t))
    y=np.empty_like(x); s=0.0
    for k,v in enumerate(x): s+=c[k]*(v-s); y[k]=s
    return y*np.minimum(1,np.minimum(t/1.0,(d-t)/1.5))
for i,n in enumerate(['Mesa Wind','Dust Devil','Night Desert','Ghost Town Gust']): save(DD,'Tumbleweed Textures','Wind',f'Texture - {n}',wind(i),stereo_w=0.8)
# --- Area 51 Instruments: Theremin Prairie
def theremin(m,d=2.2,glide=0):
    t=t_(d); f=hz(m)*(1+0.012*np.sin(2*np.pi*5.5*t))*2**(glide*np.minimum(1,t/0.4)/12)
    y=np.sin(2*np.pi*np.cumsum(f)/SR)+0.15*np.sin(4*np.pi*np.cumsum(f)/SR)
    return y*np.minimum(1,t/0.12)*np.minimum(1,(d-t)/0.4)
for nm,m in [('C',60),('D',62),('E',64),('F',65),('G',67),('A',69),('B',71),('Eb',63),('Ab',68),('Bb',70)]:
    save(A51,'Theremin Prairie','Theremin Notes',f'Theremin Note {nm}',theremin(m),stereo_w=0.5)
for i,(m,g) in enumerate([(62,12),(69,-7),(64,5),(57,12),(72,-12)]): save(A51,'Theremin Prairie','Theremin Glides',f'Theremin Glide {i+1:02d}',theremin(m,2.5,g),stereo_w=0.5)
def subbass(m):
    d=1.6; t=t_(d); f=hz(m); y=np.sin(2*np.pi*f*t)+0.3*np.tanh(3*np.sin(2*np.pi*f*t))
    return y*np.minimum(1,t/0.005)*np.exp(-t/0.9)
for nm,m in [('C',36),('D',38),('E',40),('F',41),('G',43),('A',45),('Bb',46)]: save(A51,'Saucer Bass','Sub Notes',f'Saucer Sub {nm}',subbass(m))
def wob(bpm,m,i):
    bars=2; d=60/bpm*4*bars; t=t_(d); f=hz(m); lfo=0.5+0.5*np.sin(2*np.pi*(bpm/60/(2 if i%2 else 1))*t)
    x=np.sign(np.sin(2*np.pi*f*t))*0.6+np.sin(2*np.pi*f*t)
    y=np.empty_like(x); s=0.0
    for k,v in enumerate(x): s+=(0.01+0.2*lfo[k]**2)*(v-s); y[k]=s
    return y
for i,(bpm,nm,m) in enumerate([(90,'D',38),(120,'E',40),(100,'A',45),(124,'F',41)]): save(A51,'Saucer Bass','Wobble Loops',f'Saucer Wobble {i+1:02d} - {bpm} BPM {nm} Min',wob(bpm,m,i))
# --- Midnight Rodeo: Saloon Piano Probes + Lasso Lasers
def piano(ms,d=2.5):
    t=t_(d); y=0
    for m in ms:
        f=hz(m)
        for h,a in [(1,1),(2,0.5),(3,0.25),(4,0.12),(5,0.06)]: y=y+a*np.sin(2*np.pi*f*h*1.0007**h*t+h)*np.exp(-t/(0.9/h**0.6))
    y=y+0.3*hp(noise(d),0.5)*np.exp(-t/0.004)
    detune=np.roll(y,int(SR*0.0007)); return (y+0.6*detune)*np.minimum(1,t/0.002)
for nm,m in [('C',60),('D',62),('E',64),('G',67),('A',69)]: save(MR,'Saloon Piano Probes','Single Notes',f'Saloon Piano {nm}',piano([m]))
for nm,ms in [('Am',[57,60,64]),('Dm',[50,57,62,65]),('Em',[52,59,64,67]),('C',[48,55,60,64]),('G',[43,55,59,62]),('F',[41,53,57,60])]:
    save(MR,'Saloon Piano Probes','Chords',f'Saloon Chord {nm}',piano(ms,3.0),stereo_w=0.4)
def zap(i):
    d=0.35+0.05*(i%3); t=t_(d); f=(3000-2500*np.minimum(1,t/(0.15+0.03*i)))*(1+0.2*np.sin(2*np.pi*40*t))
    return np.sin(2*np.pi*np.cumsum(f)/SR)*np.exp(-t/0.12)
for i in range(8): save(MR,'Lasso Lasers','Zaps',f'Laser Zap - Lasso {i+1:02d}',zap(i))
def riser(i):
    d=3.0; t=t_(d); f=200*2**(t/d*(3+i)); y=np.sin(2*np.pi*np.cumsum(f)/SR)*np.minimum(1,t/d)**2+0.3*hp(noise(d),0.3)*np.minimum(1,t/d)**3
    return y
for i in range(4): save(MR,'Lasso Lasers','Risers',f'Riser - Tractor Beam Up {i+1:02d}',riser(i),stereo_w=0.6)
def beam(i):
    d=4.0; t=t_(d); y=sum(np.sin(2*np.pi*(110*(k+1)+i*7)*t+np.sin(2*np.pi*0.3*k*t)) for k in range(6))*0.2
    y*=0.6+0.4*np.sin(2*np.pi*(4+i)*t); return y*np.minimum(1,np.minimum(t/0.5,(d-t)/0.8))
for i in range(4): save(MR,'Lasso Lasers','Beams',f'Hover Beam {i+1:02d}',beam(i),stereo_w=0.9)
# --- Mine: Back Porch Field Recordings
def crickets(i):
    d=5.0; t=t_(d); y=np.zeros(len(t))
    for c in range(4):
        rate=2.5+c*0.7+i*0.2; f=4200+c*350
        g=(np.sin(2*np.pi*rate*t+c)>0.6)*np.sin(2*np.pi*48*t)**2
        y+=np.sin(2*np.pi*f*t)*g*0.3
    return y+0.02*onepole(noise(d),0.05)
for i in range(3): save(MINE,'Back Porch Field Recordings','Crickets',f'Back Porch Crickets {i+1:02d}',crickets(i),stereo_w=0.7)
def howl(i):
    d=3.0; t=t_(d); f=hz(64+i*2)*(1+0.35*np.sin(np.pi*np.minimum(1,t/2.4)))*(1+0.01*np.sin(2*np.pi*6*t))
    return (np.sin(2*np.pi*np.cumsum(f)/SR)+0.3*np.sin(4*np.pi*np.cumsum(f)/SR))*np.sin(np.pi*np.minimum(1,t/2.6))**2
for i in range(3): save(MINE,'Back Porch Field Recordings','Coyote Calls',f'Coyote Call {i+1:02d}',howl(i),stereo_w=0.5)
n=sum(len(f) for _,_,f in os.walk(ROOT)); print(n,'files')
