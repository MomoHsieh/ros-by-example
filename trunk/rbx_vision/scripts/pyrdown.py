#!/usr/bin/env python


""" pyrdown.py - Version 1.0 2012-02-28

    Run the OpenCV pyrDown() function on an input image and display the result 
507px-Mona_Lisa.jpg
"""
import cv2.cv as cv
import cv2
import numpy as np

help_message = '''USAGE: pyrdown.py [<image>] [<n_pyr>]

'''

if __name__ == '__main__':
    import sys
    try:
        image_fn = sys.argv[1]
    except:
        image_fn = "test_images/mona_face.jpg"
        print help_message
        
    try:
        n_pyr = int(sys.argv[2])
    except:
        n_pyr = 2
    
    image = cv2.imread(image_fn, cv.CV_LOAD_IMAGE_COLOR)
    
    pyrdown_image = image.copy()
    
    for i in range(n_pyr):
        pyrdown_image = cv2.pyrDown(pyrdown_image)
    
    display_image = cv2.resize(pyrdown_image, (image.shape[1], image.shape[0]))

    cv2.imshow("Source Image", image)
    cv2.imshow("PyrDown 3 Times", pyrdown_image)
    cv2.imshow("Magnified PyrDown", display_image)
    
#    cv2.imwrite("mona_lisa_face.png", image)
#    cv2.imwrite("mona_lisa_face_pyr2.png", pyrdown_image)
#    cv2.imwrite("mona_lisa_face_pyr2_mag.png", display_image)
    
    cv2.waitKey()
    
    