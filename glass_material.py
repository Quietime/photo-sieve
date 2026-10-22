"""Cached progressive blur of UI-only material; never accepts application photographs."""
from functools import lru_cache
from PIL import Image, ImageDraw, ImageFilter


def progressive_blur(source, maximum=10., levels=7):
    """Interpolate Gaussian radii smoothly downwards, without band or tile seams.

    Used only on the synthetic glass reflection below. Keeping this operation outside
    paintEvent bounds work to material creation rather than every animation frame.
    """
    if maximum < 0 or levels < 2:
        raise ValueError('Expected non-negative radius and at least two levels')
    layers=[source.filter(ImageFilter.GaussianBlur(maximum*i/(levels-1))) for i in range(levels)]
    result=Image.new(source.mode,source.size)
    width,height=source.size
    for y in range(height):
        position=y/max(1,height-1)
        position=position*position*(3-2*position)*(levels-1)
        lower=min(levels-2,int(position)); weight=position-lower
        band=(0,y,width,y+1)
        result.paste(Image.blend(layers[lower].crop(band),layers[lower+1].crop(band),weight),(0,y))
    return result


@lru_cache(maxsize=12)
def material_bytes(width, height):
    """Bounded, reusable RGBA texture. Its neutral light never borrows photo colours."""
    width=max(8,min(1024,width));height=max(8,min(192,height))
    source=Image.new('RGBA',(width,height),(0,0,0,0));draw=ImageDraw.Draw(source)
    # A restrained reflection rolls from a crisp upper lip into a diffuse lower field.
    draw.rounded_rectangle((width*.12,-height*.48,width*.88,height*.16),radius=height*.22,fill=(216,232,250,26))
    draw.polygon([(width*.65,0),(width*.72,0),(width*.56,height),(width*.44,height)],fill=(216,232,250,10))
    blurred=progressive_blur(source,min(14.,height*.23))
    return blurred.tobytes()
