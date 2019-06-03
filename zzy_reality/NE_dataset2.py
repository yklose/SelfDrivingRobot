import copy
import logging
import os
import torch.utils.data
import torchvision
from PIL import Image
import torchvision.transforms as transforms_pytorch
import math

from openpifpaf import transforms
from openpifpaf import utils
from openpifpaf.datasets import collate_images_targets_meta

#import PR_pillow_testing
#from skimage import measure                        
#from shapely.geometry import Polygon, MultiPolygon 

import numpy as np
import random

ANNOTATIONS_TRAIN = 'data-mscoco/annotations/instances_train2017.json'
ANNOTATIONS_VAL = 'data-mscoco/annotations/instances_val2017.json'
IMAGE_DIR_TRAIN = 'data-mscoco/images/train2017/'
IMAGE_DIR_VAL = 'data-mscoco/images/val2017/'

################################################################################
# TODO:                                                                        #
# - Create dataset class modeled after CocoKeypoints in the official           #
#   OpenPifPaf repo                                                            #
# - Modify to take all categories of COCO (CocoKeypoints uses only the human   #
#   category)                                                                  #
# - Using the bounding box and class labels, create a new ground-truth         #
#   annotation that can be used for detection                                  #
#   (using a single keypoint per class, being the center of the bounding box)  #
#                                                                              #
# Hint: Use the OpenPifPaf repo for reference                                  #
#                                                                              #
################################################################################
class CocoKeypoints(torch.utils.data.Dataset):
    
    def __init__(self, root, annFile, image_transform=None, target_transforms=None, preprocess=None, horzontalflip=None):
        from pycocotools.coco import COCO
        self.root = root
        self.coco = COCO(annFile)
        
        # get all images - not filter
        
        self.cat_ids = self.coco.getCatIds()
        self.ids = self.coco.getImgIds()
        #self.filter_for_box_annotations()
        #self.ids = self.ids[:5]
        img_id = random.randint(1,49)
        self.target_img = Image.open("/home/zyi/reality/clothes/"+str(img_id)+".jpg", 'r').convert("RGB")
        print('Images: {}'.format(len(self.ids)))

        self.preprocess = preprocess or transforms.Normalize()
        self.image_transform = image_transform or transforms.image_transform
        self.target_transforms = target_transforms

        self.log = logging.getLogger(self.__class__.__name__)
            

    def __getitem__(self,index):
        """"Important variables:
        image_info: created by coco.loadImgs(), It has 'file_name' dict to load our file
        """
        image_id = self.ids[index]
        image_info = self.coco.loadImgs(image_id)[0]
        self.log.debug(image_info)

        with open(os.path.join(self.root, image_info['file_name']), 'rb') as f:
            image = Image.open(f).convert('RGB')

        meta_init = {
            'dataset_index': index,
            'image_id': image_id,
            'file_name': image_info['file_name'],
        }
        if 'flickr_url' in image_info:
            _, flickr_file_name = image_info['flickr_url'].rsplit('/', maxsplit=1)
            flickr_id, _ = flickr_file_name.split('_', maxsplit=1)
            meta_init['flickr_full_page'] = 'http://flickr.com/photo.gne?id={}'.format(flickr_id)

        image, anns = paste_img(image, self.target_img, image_id)

        # preprocess image and annotations
        image, anns, meta = self.preprocess(image, anns)
        if isinstance(image, list):
            return self.multi_image_processing(image, anns, meta, meta_init)

        return self.single_image_processing(image, anns, meta, meta_init)

    def multi_image_processing(self, image_list, anns_list, meta_list, meta_init):
        return list(zip(*[
            self.single_image_processing(image, anns, meta, meta_init)
            for image, anns, meta in zip(image_list, anns_list, meta_list)
        ]))

    def single_image_processing(self, image, anns, meta, meta_init):
        meta.update(meta_init)

        # transform image
        original_size = image.size
        image = self.image_transform(image)
        assert image.size(2) == original_size[0]
        assert image.size(1) == original_size[1]

        # mask valid
        #valid_area = meta['valid_area']
        #utils.mask_valid_area(image, valid_area)

        self.log.debug(meta)

        # transform targets
        if self.target_transforms is not None:
            anns = [t(anns, original_size) for t in self.target_transforms]

        return image, anns, meta
    
    def __len__(self):
        return len(self.ids)
    



def train_cli(parser):
    group = parser.add_argument_group('dataset and loader')
    group.add_argument('--train-annotations', default=ANNOTATIONS_TRAIN)
    group.add_argument('--train-image-dir', default=IMAGE_DIR_TRAIN)
    group.add_argument('--val-annotations', default=ANNOTATIONS_VAL)
    group.add_argument('--val-image-dir', default=IMAGE_DIR_VAL)
    group.add_argument('--pre-n-images', default=8000, type=int,
                       help='number of images to sampe for pretraining')
    group.add_argument('--n-images', default=None, type=int,
                       help='number of images to sampe')
    group.add_argument('--loader-workers', default=2, type=int,
                       help='number of workers for data loading')
    group.add_argument('--batch-size', default=8, type=int,
                       help='batch size')


def train_factory(args, preprocess, target_transforms):
    """This function prepares the data and directly give it back to train.py
    input:
    1. args,
    2. preprocess
    3. target_transforms
    output: train_loader, val_loader, pre_train_loader
    utilities:
        CocoKeypoints, which is a sub class of torch.utils.data.Dataset,
        which generates dataset for training and val.

    """
    train_data =  CocoKeypoints(
        root=args.train_image_dir,
        annFile=args.train_annotations,
        preprocess=preprocess,
        image_transform=transforms.image_transform_train,
        target_transforms=target_transforms,  
    )

    np.random.seed(100)
    # use random number to use only 20k but not 118k training images.
    train_loader = torch.utils.data.DataLoader(
        torch.utils.data.Subset(train_data, np.random.choice(len(train_data),20000)),
        batch_size=args.batch_size, shuffle=not args.debug, pin_memory=args.pin_memory, 
        num_workers=args.loader_workers, drop_last=True, collate_fn=collate_images_targets_meta)

    val_data = CocoKeypoints(
        root=args.val_image_dir,
        annFile=args.val_annotations,
        preprocess=preprocess,
        image_transform=transforms.image_transform_train,
        target_transforms=target_transforms,
    )

    val_loader = torch.utils.data.DataLoader(
        torch.utils.data.Subset(val_data, np.random.choice(len(val_data),5000)),
        batch_size=args.batch_size, shuffle=False,
        pin_memory=args.pin_memory, num_workers=args.loader_workers, drop_last=True,
        collate_fn=collate_images_targets_meta)
    
    pre_train_data = CocoKeypoints(
        root=args.train_image_dir,
        annFile=args.train_annotations,
        preprocess=preprocess,
        image_transform=transforms.image_transform_train,
        target_transforms=target_transforms,
        
    )
    pre_train_loader = torch.utils.data.DataLoader(
        torch.utils.data.Subset(pre_train_data, np.random.choice(len(pre_train_data),1000)),
        batch_size=args.batch_size, shuffle=True,
        pin_memory=args.pin_memory, num_workers=args.loader_workers, drop_last=True,
        collate_fn=collate_images_targets_meta)

    return train_loader, val_loader, pre_train_loader

def paste_img(background, target_img, image_id):
    transform_train = transforms_pytorch.Compose([
        transforms_pytorch.ColorJitter(brightness=0.4, contrast=0.2, saturation=0.1, hue=0.01), ## Modify: different values
        transforms_pytorch.RandomHorizontalFlip(0.5), ## Modify: You can remove
        transforms_pytorch.RandomAffine(math.pi/6, translate=None, shear=None, resample=False) ## Modify + A fillcolor='white' can be added as argument
    ])
    

    bg_w, bg_h = background.size
    img_size_w = bg_w
    img_size_h = bg_h
    min_object_h = bg_h//16 ## Modify: different scale
    max_object_h = bg_h//2  ## Modify: different scale
    min_object_w = bg_w//16 ## Modify: different scale
    max_object_w = bg_w//2  ## Modify: different scale



    # paste target image
    image_annotations = []
    is_bbox = np.random.rand() > 0.2 ## Modify: different probabilities
    if (is_bbox == False):
        x,y,h,w,x2,y2,h2,w2=0, 0, 0, 0, 0, 0, 0, 0
    else:
        target_img_i = target_img.copy()

        # transform
        target_img_i = transform_train(target_img_i)
        target_img_i = RandomPerspective(target_img_i, p =0.6)

        # resize the target_img_i
        img_w, img_h = target_img_i.size
        scale = img_w / img_h
        h = np.random.randint(min_object_h, max_object_h)
        w = int(h*scale)
        
        if w > (bg_w - 1):
            w = np.random.randint(min_object_w, max_object_w)
            h = int(w/scale)

        target_img_i = target_img_i.resize((w,h))
        x = np.random.randint(0, (img_size_w - w) if (img_size_w - w)>0 else 1)
        y = np.random.randint(0, (img_size_h - h) if (img_size_h - h)>0 else 1)

        background.paste(target_img_i, (x,y))

    image_annotations.append({
                'image_id': image_id,
                'category_id': 0,
                'keypoints': [x+w//2, y+h//2,2 if is_bbox else 0],
                'num_keypoints' : 1 if is_bbox else 0,
                'bbox': [x, y, w, h],
                'iscrowd': 0,
                'segmentation': 0,
            })

    return background, image_annotations

def RandomPerspective(image, distortion_scale=0.5, p=0.5):
    """Performs Perspective transformation of the given PIL Image randomly with a given probability.

    Args:
        interpolation : Default- Image.BICUBIC

        p (float): probability of the image being perspectively transformed. Default value is 0.5

        distortion_scale(float): it controls the degree of distortion and ranges from 0 to 1. Default value is 0.5.

    """
    if random.random() < p:
        width, height = image.size
        startpoints, endpoints = get_params(width, height, distortion_scale)
        coeffs = _get_perspective_coeffs(startpoints, endpoints)
        return image.transform((width, height), Image.PERSPECTIVE, coeffs, Image.BICUBIC)
    return image

def get_params(width, height, distortion_scale):
    """Get parameters for ``perspective`` for a random perspective transform.

    Args:
        width : width of the image.
        height : height of the image.

    Returns:
        List containing [top-left, top-right, bottom-right, bottom-left] of the orignal image,
        List containing [top-left, top-right, bottom-right, bottom-left] of the transformed image.
    """
    half_height = int(height / 2)
    half_width = int(width / 2)
    topleft = (random.randint(0, int(distortion_scale * half_width)),
                random.randint(0, int(distortion_scale * half_height)))
    topright = (random.randint(width - int(distortion_scale * half_width) - 1, width - 1),
                random.randint(0, int(distortion_scale * half_height)))
    botright = (random.randint(width - int(distortion_scale * half_width) - 1, width - 1),
                random.randint(height - int(distortion_scale * half_height) - 1, height - 1))
    botleft = (random.randint(0, int(distortion_scale * half_width)),
                random.randint(height - int(distortion_scale * half_height) - 1, height - 1))
    startpoints = [(0, 0), (width - 1, 0), (width - 1, height - 1), (0, height - 1)]
    endpoints = [topleft, topright, botright, botleft]
    return startpoints, endpoints

def _get_perspective_coeffs(pb, pa):
    """Helper function to get the coefficients (a, b, c, d, e, f, g, h) for the perspective transforms.

    In Perspective Transform each pixel (x, y) in the orignal image gets transformed as,
     (x, y) -> ( (ax + by + c) / (gx + hy + 1), (dx + ey + f) / (gx + hy + 1) )

    Args:
        List containing [top-left, top-right, bottom-right, bottom-left] of the orignal image,
        List containing [top-left, top-right, bottom-right, bottom-left] of the transformed
                   image
    Returns:
        octuple (a, b, c, d, e, f, g, h) for transforming each pixel.
    """
    matrix = []
    for p1, p2 in zip(pa, pb):
        matrix.append([p1[0], p1[1], 1, 0, 0, 0, -p2[0]*p1[0], -p2[0]*p1[1]])
        matrix.append([0, 0, 0, p1[0], p1[1], 1, -p2[1]*p1[0], -p2[1]*p1[1]])

    A = np.matrix(matrix, dtype=np.float)
    B = np.array(pb).reshape(8)

    res = np.dot(np.linalg.inv(A.T * A) * A.T, B)
    return np.array(res).reshape(8)