import numpy as np
import scipy.signal
from fractions import Fraction
import math

def test_pitch_shifter():
    n_in = 960
    PITCH_SHIFT = 1.3
    
    frac = Fraction(1.0 / PITCH_SHIFT).limit_denominator(100)
    _ps_up = frac.numerator
    _ps_down = frac.denominator
    max_rate = max(_ps_up, _ps_down)
    f_c = 1.0 / max_rate
    half_len = 10 * max_rate
    _ps_window = scipy.signal.firwin(2 * half_len + 1, f_c, window=('kaiser', 5.0))
    
    _ps_carry_overlap = np.zeros(64, dtype=np.float32)
    
    def do_pitch_shift_chunk(chunk_arr: np.ndarray) -> np.ndarray:
        nonlocal _ps_carry_overlap
        work = np.concatenate([_ps_carry_overlap, chunk_arr])
        _ps_carry_overlap = chunk_arr[-64:]
        
        stretched = scipy.signal.resample_poly(work, _ps_up, _ps_down, window=_ps_window)
        discard = int(round(64.0 * _ps_up / _ps_down))
        output = stretched[discard:]
        
        if len(output) < n_in:
            output = np.pad(output, (0, n_in - len(output)), mode='edge')
        elif len(output) > n_in:
            output = output[:n_in]
        return output

    n_read = max(1, int(round(n_in * PITCH_SHIFT)))
    print(f"PITCH_SHIFT={PITCH_SHIFT}, n_read={n_read}, up={_ps_up}, down={_ps_down}")
    
    # Simulate 5 chunks
    dummy_input = np.random.randn(n_read * 5).astype(np.float32)
    for i in range(5):
        chunk = dummy_input[i*n_read : (i+1)*n_read]
        out_chunk = do_pitch_shift_chunk(chunk)
        print(f"Chunk {i}: input_len={len(chunk)}, output_len={len(out_chunk)}")
        assert len(out_chunk) == n_in, f"Length mismatch: {len(out_chunk)} != {n_in}"
        
    print("SUCCESS: Pitch shifter output length is strictly locked to n_in!")

if __name__ == "__main__":
    test_pitch_shifter()
