"""Functions exposed: segment(), evaluate_nuclei_segmentation()."""

from types import MappingProxyType
from typing import Any, List, Union, Mapping, Optional
import abc

from anndata import AnnData

import numpy as np
import xarray as xr

import skimage

from squidpy._docs import d, inject_docs
from squidpy.im.crop import uncrop_img
from squidpy.im.object import ImageContainer
from squidpy.constants._constants import SegmentationBackend


# TODO: dead code?
def evaluate_nuclei_segmentation(adata: AnnData, copy: bool = False, **kwargs: Any) -> Optional[AnnData]:
    """
    Perform basic nuclei segmentation evaluation.

    Metrics on H&E signal in segments vs outside.

    Attrs:
        adata:
        copy:
        kwargs:
    """


class SegmentationModel:
    """
    Base class for segmentation models.

    Contains core shared functions related contained to cell and nuclei segmentation.
    Specific segmentation models can be implemented by inheriting from this class.

    This class is not instantiated by user but used in the background by the functional API.

    Parameters
    ----------
    model
        Underlying segmentation model.
    """

    def __init__(
        self,
        model: Any,
    ):
        self.model = model

    @d.get_full_description(base="segment")
    @d.get_sections(base="segment", sections=["Parameters", "Returns"])
    @d.dedent
    def segment(self, img: np.ndarray, **kwargs: Any) -> np.ndarray:
        """
        Segment an image.

        Parameters
        ----------
        %(img_hr)s

        Returns
        -------
        Segmentation mask for the high-resolution image of shape (x, y, 1).
        """
        # TODO: make sure that the dtype is correct
        return self._segment(img, **kwargs)

    # TODO: I'd rather make the public method abstract, so that its docs are seen for general user
    @abc.abstractmethod
    def _segment(self, arr: np.ndarray, **kwargs: Any) -> np.ndarray:
        pass


class SegmentationModelBlob(SegmentationModel):
    """Segmentation model based on :mod:`skimage` blob detection."""

    @d.dedent
    def _segment(self, img: np.ndarray, invert: bool = True, **kwargs: Any) -> np.ndarray:
        """
        %(segment.full_desc)s

        Parameters
        ----------
        %(segment.parameters)s
        kwargs
            Keyword arguments for :attr:`_model`.

        Returns
        -------
        %(segment.returns)s
        """  # noqa: D400
        if invert:
            img = 0.0 - img

        if self.model == "log":
            y = skimage.feature.blob_log(image=img, **kwargs)
        elif self.model == "dog":
            y = skimage.feature.blob_dog(image=img, **kwargs)
        elif self.model == "doh":
            y = skimage.feature.blob_doh(image=img, **kwargs)
        else:
            raise ValueError("did not recognize self.model %s" % self.model)
        return y


class SegmentationModelWatershed(SegmentationModel):
    """Segmentation model based on :mod:`skimage` watershed segmentation."""

    @d.dedent
    def _segment(self, arr: np.ndarray, thresh: float = 0.5, geq: bool = True, **kwargs: Any) -> np.ndarray:
        """
        %(segment.full_desc)s

        Parameters
        ----------
        %(segment.parameters)s
        thresh
             Threshold for discretization of image scale to define areas to segment.
        geq
            Treat ``thresh`` as upper or lower (greater-equal = geq) bound for defining state to segment.
        kwargs
            Keyword arguments for :attr:`_model`.

        Returns
        -------
        %(segment.returns)s
        """  # noqa: D400
        from scipy import ndimage as ndi

        from skimage.util import invert
        from skimage.feature import peak_local_max
        from skimage.segmentation import watershed

        # TODO check if threshold is in [0, 1].
        # TODO check image dtype/ranges
        # get binarized image
        if geq:
            mask = arr >= thresh
        else:
            mask = arr < thresh

        # calculate markers as maximal distanced points from background (locally)
        distance = ndi.distance_transform_edt(1 - mask)
        local_maxi = peak_local_max(distance, indices=False, footprint=np.ones((5, 5)), labels=1 - mask)
        markers = ndi.label(local_maxi)[0]
        return watershed(invert(arr), markers, mask=1 - mask)


# TODO: too long of a name
class SegmentationModelPretrainedTensorflow(SegmentationModel):
    """Segmentation model using :mod:`tensofrlow` model."""

    def __init__(self, model, **_: Any):  # type: ignore[no-untyped-def]
        import tensorflow as tf

        # TODO: maybe just check it's callable?
        assert isinstance(model, tf.keras.model.Model), "Model should be a `tensorflow.keras.model` instance."
        super().__init__(model=model)

    @d.dedent
    def _segment(self, arr: np.ndarray, **kwargs: Any) -> np.ndarray:
        """
        %(segment.full_desc)s

        Parameters
        -----------
        %(segment.parameters)s
        kwargs
            Keyword arguments for the :attr:`_model`.

        Returns
        -------
        %(segment.returns)s
        """  # noqa: D400
        # Uses callable tensorflow keras model.
        return self.model(arr, **kwargs)


@d.dedent
@inject_docs(m=SegmentationBackend)
def segment(
    img: ImageContainer,
    img_id: str,
    model_group: Union[str, SegmentationBackend],
    model_instance: Optional[Union[str, SegmentationModel]] = None,
    model_kwargs: Mapping[str, Any] = MappingProxyType({}),
    channel_idx: Optional[int] = None,
    xs: Optional[int] = None,
    ys: Optional[int] = None,
    key_added: Optional[str] = None,
) -> None:
    """
    %(segment.full_desc)s

    If xs and ys are defined, iterate over crops of size `(xs,ys)` and segment those.
    Recommended for large images.

    Parameters
    ----------
    %(img_container)s
    img_id
        Key of image object to segment.
    model_group
        Segmentation method to use. Available are:

            - `{m.BLOB.s!r}`: Blob extraction with :mod:`skimage`.
            - `{m.WATERSHED.s!r}`: TODO.
            - `{m.TENSORFLOW.s!r}`: :mod:`tensorflow` executable model.

    model_instance
        TODO: this logic should be refactored.
        Instance of executable segmentation model or name of specific method within ``model_group``.
    model_kwargs
        Keyword arguments for :meth:`squidpy.im.SegmentationModel.segment`.
    channel_idx
        Channel to use for segmentation.
    %(width_height)s
    key_added
        Key of new image sized array to add into img object. Defaults to ``segmented_{{model_group}}``.

    Returns
    -------
    Nothing, just updates ``img``.
    """  # noqa: D400
    channel_id = "mask"
    model_group = SegmentationBackend(model_group)

    if model_group == SegmentationBackend.BLOB:
        segmentation_model: SegmentationModel = SegmentationModelBlob(model=model_instance)
    elif model_group == SegmentationBackend.WATERSHED:
        segmentation_model = SegmentationModelWatershed(model=model_instance)
    elif model_group == SegmentationBackend.TENSORFLOW:
        segmentation_model = SegmentationModelPretrainedTensorflow(model=model_instance)
    else:
        raise NotImplementedError(model_group)

    crops, xcoord, ycoord = img.crop_equally(xs=xs, ys=ys, img_id=img_id)
    channel_slice = slice(0, crops[0].channels.shape[0]) if channel_idx is None else channel_idx
    crops = [segmentation_model.segment(x[{"channels": channel_slice}].values, **model_kwargs) for x in crops]
    # By convention, segments are numbered from 1..number of segments within each crop.
    # Next, we have to account for that before merging the crops so that segments are not confused.
    # TODO use overlapping crops to not create confusion at boundaries
    counter = 0
    for i, x in enumerate(crops):
        crop_new = x
        num_segments = np.max(x)
        crop_new[crop_new > 0] = crop_new[crop_new > 0] + counter
        counter += num_segments
        crops[i] = xr.DataArray(crop_new[np.newaxis, :, :], dims=["mask", "y", "x"])
    # TODO quickfix for img.shape here, will change this behaviour soon! img.shape should return y, x (and not x,y)
    img_segmented = uncrop_img(crops=crops, x=xcoord, y=ycoord, shape=img.shape[::-1], channel_id=channel_id)
    img_id = "segmented_" + model_group.s if key_added is None else key_added
    img.add_img(img=img_segmented, img_id=img_id, channel_id=channel_id)


@d.dedent
def segment_crops(
    img: ImageContainer,
    img_id: str,
    segmented_img_id: str,
    xs: Optional[int] = None,
    ys: Optional[int] = None,
) -> List[xr.DataArray]:
    """
    %(segment.full_desc)s

    Parameters
    ----------
    %(img_container)s
    img_id
        Key of image object to take crops from.
    segmented_img_id
        Key of image object that contains segments.
    %(width_height)s # TODO: add support as soon as crop supports this

    Returns
    -------
    Crops centred on segments.
    """  # noqa: D400
    segment_centres = [
        (
            np.mean(np.where(img.data[segmented_img_id] == i)[0]),
            np.mean(np.where(img.data[segmented_img_id] == i)[1]),
        )
        for i in np.sort(list(set(np.unique(img.data[segmented_img_id])) - {0}))
    ]
    return [img.crop_center(x=int(xi), y=int(yi), xs=xs, ys=ys, img_id=img_id) for xi, yi in segment_centres]
