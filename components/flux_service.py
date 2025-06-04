import logging
import os
from typing import Optional, Any, Union

try:
    from gradio_client.exceptions import AppError
except ImportError:
    try:
        from gradio_client.utils import AppError
    except ImportError:
        AppError = Exception

class FluxError(Exception):
    pass

class FluxGenerationError(FluxError):
    pass

class FluxClientError(FluxError):
    pass

def generate_image_with_flux(
    hf_client: Optional[Any],
    prompt: str,
    width: int,
    height: int,
    randomize_seed: bool = True,
    num_inference_steps: int = 28,   # !!! <<<---
    guidance_scale: int = 3.5   # !!! <<<---
) -> Optional[str]:
    if hf_client is None:
        logging.error("FLUX Gradio client (hf_client) is not initialized.")
        raise FluxClientError("Image generation service (FLUX client) is not available.")

    seed_text = 'Random' if randomize_seed else 'Fixed'
    logging.info(f"Initiating FLUX generation with provided prompt. "
                f"Dim: {width}x{height}, Seed: {seed_text}, Steps: {num_inference_steps}")
    
    try:
        result = hf_client.predict(
            prompt=prompt,
            randomize_seed=randomize_seed,
            width=width,
            height=height,
            num_inference_steps=num_inference_steps,
            guidance_scale=guidance_scale,   # !!! <<<---
            api_name="/infer"
        )
    except AppError as e:
        logging.error(f"Error reported by FLUX service (via Gradio client): {e}") 
        raise FluxGenerationError(f"Image generation failed: FLUX service error.") from e
    except Exception as e:
        logging.error(f"Unexpected error during FLUX hf_client.predict: {e}", exc_info=True)
        raise FluxGenerationError(f"Image generation failed with an unexpected internal error.") from e

    image_path = _extract_image_path(result)
    
    if image_path and os.path.exists(image_path):
        logging.info(f"FLUX generation successful. Temporary image path: {image_path}")
        return image_path
    
    logging.error(f"Unexpected result format or file not found from FLUX API. "
                 f"Result type: {type(result)}, Content: {str(result)[:200]}")
    raise FluxGenerationError("Image generation failed (unexpected API response or file issue from FLUX).")

def _extract_image_path(result: Any) -> Optional[str]:
    if isinstance(result, str):
        return result
    if isinstance(result, (tuple, list)) and result and isinstance(result[0], str):
        return result[0]
    return None