import numpy as np
import math

def pitch_shift_dual_tap(data: np.ndarray, factor: float, state: dict) -> np.ndarray:
    """
    Pitch shifts a 1D numpy array of int16 samples using a dual-tap delay line
    with a constant-power sine/cosine crossfade.
    state: a dict containing 'buffer', 'write_idx', and 'd1'
    """
    if factor == 1.0:
        return data

    # Parameters
    D_max = 512  # Maximum delay in samples (approx 21ms at 24kHz)
    N_buf = 16384  # Delay buffer size (must be larger than data length + D_max)
    
    # Initialize state if not present
    if 'buffer' not in state:
        state['buffer'] = np.zeros(N_buf, dtype=np.float64)
        state['write_idx'] = 0
        state['d1'] = 0.0

    buf = state['buffer']
    write_idx = state['write_idx']
    d1 = state['d1']
    
    out = np.zeros_like(data, dtype=np.float64)
    n = len(data)
    
    # Pre-load data into buffer to handle block processing
    for i in range(n):
        buf[write_idx] = float(data[i])
        
        # d1 sweeps between 0 and D_max.
        # To shift pitch up by factor f, delay must decrease by (f - 1) per sample.
        d1 -= (factor - 1.0)
        # Wrap d1
        while d1 < 0:
            d1 += D_max
        while d1 >= D_max:
            d1 -= D_max
            
        d2 = d1 + D_max / 2.0
        if d2 >= D_max:
            d2 -= D_max
            
        # Tap 1 read index
        r1 = write_idx - d1
        if r1 < 0:
            r1 += N_buf
        idx1_f = int(r1)
        idx1_c = (idx1_f + 1) % N_buf
        frac1 = r1 - idx1_f
        s1 = (1.0 - frac1) * buf[idx1_f] + frac1 * buf[idx1_c]
        
        # Tap 2 read index
        r2 = write_idx - d2
        if r2 < 0:
            r2 += N_buf
        idx2_f = int(r2)
        idx2_c = (idx2_f + 1) % N_buf
        frac2 = r2 - idx2_f
        s2 = (1.0 - frac2) * buf[idx2_f] + frac2 * buf[idx2_c]
        
        # Sine/Cosine crossfade weights for constant power
        theta = (d1 / D_max) * (np.pi / 2.0)
        w1 = math.sin(theta)
        w2 = math.cos(theta)
        
        # Output sample (constant power sum)
        out[i] = w1 * s1 + w2 * s2
        
        # Advance write index
        write_idx = (write_idx + 1) % N_buf
        
    state['write_idx'] = write_idx
    state['d1'] = d1
    
    return np.clip(out, -32768, 32767).astype(np.int16)

# Test with a simple sine wave
if __name__ == '__main__':
    sample_rate = 24000
    freq = 440.0
    duration = 0.5
    t = np.linspace(0, duration, int(sample_rate * duration), endpoint=False)
    sine_wave = (np.sin(2 * np.pi * freq * t) * 32767).astype(np.int16)
    
    # Process in chunks of 1024
    chunk_size = 1024
    state = {}
    shifted_chunks = []
    
    factor = 1.5  # shift up by 1.5x
    
    for i in range(0, len(sine_wave), chunk_size):
        chunk = sine_wave[i:i+chunk_size]
        if len(chunk) < chunk_size:
            chunk = np.pad(chunk, (0, chunk_size - len(chunk)))
        shifted = pitch_shift_dual_tap(chunk, factor, state)
        shifted_chunks.append(shifted)
        
    result = np.concatenate(shifted_chunks)
    print("Processed successfully! Output size:", len(result))
