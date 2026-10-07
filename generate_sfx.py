import wave
import math
import struct
import os

def generate_sfx(filename, type='pop'):
    sample_rate = 44100
    if type == 'pop':
        duration = 0.1
    elif type == 'whoosh':
        duration = 0.5
    elif type == 'ding':
        duration = 1.0
    else:
        duration = 0.5

    num_samples = int(sample_rate * duration)
    
    with wave.open(filename, 'w') as wav_file:
        wav_file.setnchannels(1) # mono
        wav_file.setsampwidth(2) # 2 bytes = 16 bit
        wav_file.setframerate(sample_rate)
        
        for i in range(num_samples):
            t = float(i) / sample_rate
            
            if type == 'pop':
                # Quick burst of noise or sine wave fading fast
                freq = 400 + 1000 * math.exp(-t * 50)
                amplitude = 32767 * math.exp(-t * 40)
                value = int(amplitude * math.sin(2 * math.pi * freq * t))
            elif type == 'whoosh':
                # White noise with a low pass sweep or just a volume swell
                # We'll do a simple sine wave sweep for whoosh
                freq = 100 + 800 * math.sin(math.pi * t / duration)
                amplitude = 32767 * math.sin(math.pi * t / duration)
                value = int(amplitude * math.sin(2 * math.pi * freq * t))
            elif type == 'ding':
                # Clear bell sound (sine wave)
                freq = 1200
                amplitude = 32767 * math.exp(-t * 3)
                value = int(amplitude * math.sin(2 * math.pi * freq * t))
            
            # Ensure it fits in 16-bit short
            value = max(-32768, min(32767, value))
            data = struct.pack('<h', value)
            wav_file.writeframesraw(data)

if __name__ == '__main__':
    sfx_dir = os.path.join('assets', 'sfx')
    os.makedirs(sfx_dir, exist_ok=True)
    generate_sfx(os.path.join(sfx_dir, 'pop.wav'), 'pop')
    generate_sfx(os.path.join(sfx_dir, 'whoosh.wav'), 'whoosh')
    generate_sfx(os.path.join(sfx_dir, 'ding.wav'), 'ding')
    print("SFX generated!")
