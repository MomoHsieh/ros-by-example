#!/usr/bin/env python

""" adaptive_face_tracker.py - Version 1.0 2012-03-10

    Combines the OpenCV Haar face detector with Good Features to Track and Lucas-Kanade
    optical flow tracking. Build a collection of template classifiers on the fly.
     
"""

import roslib
roslib.load_manifest('pi_video_tracker')
import rospy
from ros2opencv2 import ROS2OpenCV2
import os, glob, errno
import sys
import cv2
import cv2.cv as cv
from sensor_msgs.msg import Image, RegionOfInterest 
import numpy as np
from time import clock
from math import sqrt, isnan
import numpy as np
from dynamic_reconfigure.server import Server
from pi_video_tracker.cfg import PiVideoTrackerConfig

class AdaptiveFaceTracker(ROS2OpenCV2):
    def __init__(self, node_name):
        ROS2OpenCV2.__init__(self, node_name)
        
        self.node_name = node_name
        
        # Fire up the dynamic_reconfigure server
        dyn = Server(PiVideoTrackerConfig, self.dynamic_reconfigure_callback)

        self.auto_face_tracking = rospy.get_param("~auto_face_tracking", True)
        self.n_faces = rospy.get_param("~n_faces", 1)
        self.show_text = rospy.get_param("~show_text", True)
        self.show_add_drop = rospy.get_param("~show_add_drop", False)
        self.feature_size = rospy.get_param("~feature_size", 1)
        self.use_depth_for_tracking = rospy.get_param("~use_depth_for_tracking", False)
        self.auto_min_keypoints = rospy.get_param("~auto_min_keypoints", True)
        self.min_keypoints = rospy.get_param("~min_keypoints", 50) # Used only if auto_min_keypoints is False
        self.abs_min_keypoints = rospy.get_param("~abs_min_keypoints", 6)
        self.std_err_xy = rospy.get_param("~std_err_xy", 0.5) 
        self.pct_err_z = rospy.get_param("~pct_err_z", 0.42) 
        self.max_mse = rospy.get_param("~max_mse", 10000)
        self.add_keypoint_distance = rospy.get_param("~add_keypoint_distance", 10)
        self.flip_image = rospy.get_param("~flip_image", False)
        self.keypoint_type = rospy.get_param("~keypoint_type", 0) # 0 = Good Features to Track, 1 = SURF
        self.get_surf_also = False
        self.expand_roi_init = rospy.get_param("~expand_roi", 1.02)
        self.expand_roi = self.expand_roi_init
        
        self.template_match_threshold = rospy.get_param("~template_match_threshold", 0.5)
        self.template_n_pyr = rospy.get_param("~template_n_pyr", 2)
        self.template_min_size = rospy.get_param("~template_min_size", 75)
        self.template_scale_factor = rospy.get_param("~template_scale_factor", 1.1)
        self.template_scale_and_rotate = rospy.get_param("~template_scale_and_rotate", True)
        self.template_match_interval = rospy.get_param("~template_match_interval", 20)
        
        self.face_dir = "/home/patrick/Dropbox/Robotics/ros/pi-robot-ros-pkg/experimental/ros_by_example/rbx_pi_vision/pi_video_tracker/models/templates/faces/melody"
        mkdir_p(self.face_dir)
        
        self.templates = list()
        
        for template_file in glob.glob(os.path.join(self.face_dir, '*')):
            template = cv2.imread(template_file)
            self.templates.append(template)
        
        self.use_classifier = True
        self.classifier_initialized = True
        self.redetect_index = 0
        
        # Good Feature paramters
        self.gf_maxCorners = rospy.get_param("~gf_maxCorners", 200)
        self.gf_qualityLevel = rospy.get_param("~gf_qualityLevel", 0.01)
        self.gf_minDistance = rospy.get_param("~gf_minDistance", 5)
        self.gf_blockSize = rospy.get_param("~gf_blockSize", 3)
        self.gf_useHarrisDetector = rospy.get_param("~gf_useHarrisDetector", False)
        self.gf_k = rospy.get_param("~gf_k", 0.04)
        
        # LK parameters
        self.lk_winSize = rospy.get_param("~lk_winSize", (10, 10))
        self.lk_maxLevel = rospy.get_param("~lk_maxLevel", 3)
        self.lk_criteria = rospy.get_param("~lk_criteria", (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 20, 0.01))
        self.lk_derivLambda = rospy.get_param("~lk_derivLambda", 0.1)
        
        # Haar face detector parameters
        self.haar_scaleFactor = 1.5
        self.haar_minNeighbors = 1
        self.haar_minSize = (20, 20)
        self.haarFlags = cv.CV_HAAR_DO_CANNY_PRUNING
        self.haar_image_scale = 2
        
        self.gf_params = dict( maxCorners = self.gf_maxCorners,
                               qualityLevel = self.gf_qualityLevel,
                               minDistance = self.gf_minDistance,
                               blockSize = self.gf_blockSize,
                               useHarrisDetector = self.gf_useHarrisDetector,
                               k = self.gf_k )
        
        self.lk_params = dict( winSize  = self.lk_winSize, 
                  maxLevel = self.lk_maxLevel, 
                  criteria = self.lk_criteria,
                  derivLambda = self.lk_derivLambda )
        
        self.haar_params = dict ( scaleFactor = self.haar_scaleFactor,
                                  minNeighbors = self.haar_minNeighbors,
                                  minSize = self.haar_minSize,
                                  flags = self.haarFlags )
        
        # SURF parameters
        self.surf_hessian_quality = rospy.get_param("~surf_hessian_quality", 100)
        self.surf = cv2.SURF(self.surf_hessian_quality, 3, 1)
        
        self.use_depth_for_detection = rospy.get_param("~use_depth_for_detection", False)
        self.fov_width = rospy.get_param("~fov_width", 1.094)
        self.fov_height = rospy.get_param("~fov_height", 1.094)
        self.max_object_size = rospy.get_param("~max_face_size", 0.28)

        self.frame_index = 0
        self.add_keypoints_interval = 1
        self.drop_keypoints_interval = 1
        self.keypoints = []

        self.detect_box = None
        self.track_box = None
        
        self.mask = None
        self.prev_grey = None
        
        # What kind of detector do we want to load
        self.detector_loaded = False
        self.last_face_box = None
        
        self.use_tracking = True
        
        
#        # Parameters for HSV filtering
#        self.smin = 72 #31
#        self.vmin = 54 #41
#        self.vmax = 255 #255
#        
#        cv.NamedWindow("HSV Parameters", 0)
#        cv.CreateTrackbar("Saturation", "HSV Parameters", self.smin, 255, self.set_smin)
#        cv.CreateTrackbar("Min Value", "HSV Parameters", self.vmin, 255, self.set_vmin)
#        cv.CreateTrackbar("Max Value", "HSV Parameters", self.vmax, 255, self.set_vmax) 

        rospy.loginfo("Waiting for video topics to become available...")

        # Wait until the image topics are ready before starting
        rospy.wait_for_message("input_rgb_image", Image)
        
        if self.use_depth_for_detection:
            rospy.wait_for_message("input_depth_image", Image)
            
        rospy.loginfo("Ready.")
        
#    def set_smin(self, pos):
#        self.smin = pos
#        
#    def set_vmin(self, pos):
#        self.vmin = pos
#        
#    def set_vmax(self, pos):
#       self.vmax = pos
        
    def dynamic_reconfigure_callback(self, config, level):
        self.template_match_threshold = config['template_match_threshold']
        self.template_match_interval = config['template_match_interval']
        self.template_npyr = config['template_npyr']

        return config
        
    def process_image(self, cv_image):
        self.redetect_index += 1
        
        # Create a numpy array version of the image as required by many of the cv2 functions
        cv_array = np.array(cv_image, dtype=np.uint8)
        
#        hsv = cv2.cvtColor(cv_array, cv2.COLOR_BGR2HSV)
#        mask = cv2.inRange(hsv, np.array((0., self.smin, self.vmin)), np.array((180., 255., self.vmax)))
#        
#        result = cv.CreateMat(cv_image.rows, cv_image.cols, cv_image.type)
#        cv.Copy(cv_image, result, cv.fromarray(mask))
#        if self.redetect_index % self.template_match_interval == 0:
#            cv.ShowImage("HSV Filtered", result)
        
 
        # Equalize the histogram to reduce lighting effects.       
        if cv_image.channels == 3:
            hsv = cv2.cvtColor(cv_array, cv2.COLOR_BGR2HSV)
            (h,s,v) = cv2.split(hsv)
            v = cv2.equalizeHist(v)
            cv2.merge((h,s,v), hsv)
            cv_array = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
            
            # Create a greyscale version of the image
            self.grey = cv2.cvtColor(cv_array, cv2.COLOR_BGR2GRAY)
        else:
            self.grey = cv2.cvtColor(cv_array, cv2.COLOR_BGR2GRAY)
            self.grey = cv2.equalizeHist(self.grey)
        
        if self.classifier_initialized and self.use_classifier:
            if self.redetect_index % self.template_match_interval == 0:
                self.redetect_index = 0
                test_box = self.match_template(cv_array, self.detect_box)
                if test_box is not None:
                    self.keypoints = []
                    #self.detect_box = test_box
                    #self.track_box = self.detect_box
                    #if self.use_tracking:
                    self.get_keypoints(test_box)
#                        if self.track_box is None:
#                            self.track_box = test_box
                    if not self.use_tracking:
                        self.track_box= test_box

                    if self.detect_box is None:
                        self.detect_box = test_box
  
        """ STEP 1. Load the face detector if appropriate """
        if self.auto_face_tracking:
            if not self.detector_loaded:
                self.detector_loaded = self.load_face_detector()
            if not self.detect_box:
                self.detect_box = self.detect_face(cv_image)
                
        """ STEP 2: Extract keypoints and initial template """
        if self.detect_box:
            if not self.track_box or not self.is_rect_nonzero(self.track_box):
                self.keypoints = []
                self.track_box = self.detect_box
                if self.use_tracking:
                    self.get_keypoints(self.track_box)
                    
            if self.prev_grey is None:
                self.prev_grey = self.grey           
                
            """ Step 3: Begin tracking """
            if self.use_tracking:
                self.track_box = self.track_keypoints()
            
            """ Step 3a: Intialize the adaptive classifier """
            if self.use_classifier and not self.classifier_initialized:
                rospy.loginfo("Initializing classifier...")
                self.classifier_initialized = self.initialize_classifier2(cv_array, self.detect_box)
            
            """ STEP 4: Drop keypoints that are too far from the main cluster """
            if self.use_tracking:
                if self.frame_index % self.drop_keypoints_interval == 0 and len(self.keypoints) > 0:
                    ((cog_x, cog_y, cog_z), mse_xy, mse_z, score) = self.drop_keypoints(self.abs_min_keypoints, self.std_err_xy, self.max_mse)
                    
                    if score == -1:
                        self.detect_box = None
                        self.track_box = None
                        return cv_image
                    
                """ STEP 5: Add keypoints if the number is getting too low """
                if self.frame_index % self.add_keypoints_interval == 0 and len(self.keypoints) < self.min_keypoints:
                    self.expand_roi = self.expand_roi_init * self.expand_roi
                    self.add_keypoints(self.track_box)
                else:
                    self.frame_index += 1
                    self.expand_roi = self.expand_roi_init
                
#            """ Step 6: Check to see if we need to add a new template """
#            if self.frame_index % self.template_match_interval == 0 and self.track_box and self.is_rect_nonzero(self.track_box): 
#                #self.check_templates(cv_image, self.track_box)
#                max_score = 0
#                for template in self.templates:
#                    try:
#                        (score, track_box) = self.match_template(cv_image, template, self.track_box)
#                    except:
#                        continue
#                    if score > max_score:
#                        max_score = score
#                        best_track_box = track_box
#                        
#                #rospy.loginfo("Best Score: " + str(max_score))
#                
#                if max_score > 0.7:
#                    pass
#                    #self.keypoints = list()
#                    #self.track_box = best_track_box
#                    #self.detect_box = None
#                    #(center, size, angle) = self.track_box
#                    #pt1 = (int(center[0] - size[0] / 2), int(center[1] - size[1] / 2))
#                    #self.detect_box = (pt1[0], pt1[1], size[0], size[1])
#                elif max_score > 0:
#                    self.templates.append(self.get_template(cv_image, self.track_box))
#                    rospy.loginfo("Adding Template: " + str(len(self.templates) + 1))  
                         
        else:         
            self.keypoints = []
            self.track_box = None
            
        self.prev_grey = self.grey

        # Process any special keyboard commands for this module
        if 32 <= self.keystroke and self.keystroke < 128:
            cc = chr(self.keystroke).lower()
            if cc == 'c':
                self.keypoints = []
                self.track_box = None
                self.detect_box = None
            elif cc == 'a':
                self.auto_face_tracking = not self.auto_face_tracking
                if self.auto_face_tracking:
                    self.keypoints = []
                    self.track_box = None
                    self.detect_box = None
                 
        return cv.fromarray(cv_array)
    
    def match_template(self, search_array, track_box):
        search_image = cv.fromarray(search_array)
        track_box = None
        if track_box is None:
            track_box = (0, 0, self.frame_size[0], self.frame_size[1])
        try:
            [center, size, angle] = track_box
            pt1 = [int(center[0] - size[0] / 2), int(center[1] - size[1] / 2)]
            pt2 = [int(center[0] + size[0] / 2), int(center[1] + size[1] / 2)]
            w = pt2[0] - pt1[0]
            h = pt2[1] - pt1[1]
        except:
            [x, y, w, h] = track_box
            pt1 = [x, y]
            pt2 = [x+w, y+h]
            
        self.search_factor = 2.0
        
        w_search = int(w * self.search_factor)
        h_search = int(h * self.search_factor)
                
        pt1[0] = min(self.frame_size[0], max(0, pt1[0] - int((w_search - w) / 2)))
        pt1[1] = min(self.frame_size[1], max(0, pt1[1] - int((h_search - h) / 2)))
        pt2[0] = min(self.frame_size[0], pt1[0] + w_search)
        pt2[1] = min(self.frame_size[1], pt1[1] + h_search)
        try:
            #cv.Rectangle(self.marker_image, (pt1[0], pt1[1]), (pt2[0], pt2[1]), cv.RGB(0, 0, 255), 3)
            search_image = search_image[pt1[1]:pt2[1],pt1[0]:pt2[0]]
        except:
            return None

        frame = np.array(search_image, dtype=np.uint8)
        
#        cv.NamedWindow("Search Image", cv.CV_WINDOW_AUTOSIZE)
#        cv.MoveWindow("Search Image", 800, 200)
#        cv.ShowImage("Search Image", search_image)

        # Create a copy of the frame to modify
        frame_copy = frame.copy()
        
        for i in range(self.template_n_pyr):
            frame_copy = cv2.pyrDown(frame_copy)
            
#        H,W = frame.shape[0], frame.shape[1]
#
#        # Make sure that the template image is smaller than the source
#        if W < w or H < h:
#            #rospy.loginfo( "Template image must be smaller than video frame." )
#            return None
        
        if frame.dtype != self.templates[0].dtype: 
            #rospy.loginfo("Template and video frame must have same depth and number of channels.")
            return None
                   
        template_height, template_width = self.templates[0].shape[:2]
        
        # Cycle through all scales starting with the last successful scale
        #scales = self.scales[self.last_scale:] + self.scales[:self.last_scale - 1]

        # Track which scale and rotation gives the best match
        maxScore = -1
        best_s = 1
        best_r = 0
        best_x = 0
        best_y = 0
        
        for k in range(len(self.templates)):
            if self.template_scale_and_rotate:
                """ Compute the min and max scales """
                width_ratio = float(frame.shape[0]) / self.templates[k].shape[0]
                height_ratio = float(frame.shape[1]) / self.templates[k].shape[1]
                
                max_scale = 0.9 * min(width_ratio, height_ratio)
                
                max_template_dimension = max(self.templates[k].shape[0], self.templates[k].shape[1])
                min_scale = 1.1 * float(self.template_min_size) / max_template_dimension
                
                self.scales = list()
                scale = min_scale
                while scale < max_scale:
                    self.scales.append(scale)
                    scale *= self.template_scale_factor
                #self.rotations = [-25, 0, 25]
                self.rotations = [0]
                
            else:
                self.scales = [1]
                self.rotations = [0]
                    
            for s in self.scales:
                for r in self.rotations:
                    # Scale the template by s
                    template_copy = cv2.resize(self.templates[k], (int(template_width * s), int(template_height * s)))
    
                    # Rotate the template through r degrees
                    rotation_matrix = cv2.getRotationMatrix2D((template_copy.shape[1]/2, template_copy.shape[0]/2), r, 1.0)
                    template_copy = cv2.warpAffine(template_copy, rotation_matrix, (template_copy.shape[1], template_copy.shape[0]), borderMode=cv2.BORDER_REPLICATE)
        
                    # Use pyrDown() template_n_pyr times on the scaled and rotated template
                    try:
                        for i in range(self.template_n_pyr):
                            template_copy = cv2.pyrDown(template_copy)
                    except:
                        continue
                    
                    h,w = self.templates[k].shape[0], self.templates[k].shape[1]
                    
                    # Create the results array to be used with matchTempate()
                    h,w = template_copy.shape[:2]
                    H,W = frame_copy.shape[:2]
                    
                    result_width = W - w + 1
                    result_height = H - h + 1
                    if min(result_width, result_height) <= 0:
                        continue
    
                    result_mat = cv.CreateMat(result_height, result_width, cv.CV_32FC1)
                    result = np.array(result_mat, dtype = np.float32)
                    
                    # Run matchTemplate() on the reduced images
                    cv2.matchTemplate(frame_copy, template_copy, cv2.TM_CCOEFF_NORMED, result)      
                
                    # Cubing the result exaggerates the differences
                    #result = np.abs(result)**3
                
                    # Then do a sub-maximal suppression to emphasize the peaks
                    #val, result = cv2.threshold(result, 0.01, 0, cv2.THRESH_TOZERO)
                    
                    # Find the maximum value on the result map
                    (minValue, maxValue, minLoc, maxLoc) = cv2.minMaxLoc(result)
                    
                    if maxValue > maxScore:
                        maxScore = maxValue
                        best_x, best_y = maxLoc
                        best_s = s
                        best_r = r
                        best_template = template_copy.copy()
                        self.last_scale = self.scales.index(s)
                        best_result = result.copy()
                
        # Transform back to original image sizes
        best_x *= int(pow(2.0, self.template_n_pyr))
        best_y *= int(pow(2.0, self.template_n_pyr))
        best_x += pt1[0]
        best_y += pt1[1]
        h,w = self.templates[k].shape[:2]
        h = int(h * best_s)
        w = int(w * best_s)
        best_result = cv2.resize(best_result, (int(pow(2.0, self.template_n_pyr)) * best_result.shape[1], int(pow(2.0, self.template_n_pyr)) * best_result.shape[0]))
        cv.NamedWindow("Result", cv.CV_NORMAL)
        cv.ResizeWindow("Result", 320, 240)
        cv.MoveWindow("Result", 700, 350)
        cv2.imshow("Result", best_result)
        best_template = cv2.resize(best_template, (int(pow(2.0, self.template_n_pyr)) * best_template.shape[1], int(pow(2.0, self.template_n_pyr)) * best_template.shape[0]))
        cv2.imshow("Best Template", best_template)
        
        #match_box = ((best_x + w/2, best_y + h/2), (w, h), -best_r)
        if maxScore > self.template_match_threshold:
            rospy.loginfo("Max Score: " + str(maxScore))
            return (best_x, best_y, w, h)
        else:
#            if maxScore > 0.5:
#                self.classifier_initialized = False
            return None
    
    def initialize_classifier(self, cv_array, roi):
        template = self.get_template(cv_array, roi)
        self.templates.append(template)
        rospy.loginfo("Got Initial Template")
        return True
    
    def initialize_classifier2(self, search_array, roi):
        search_image = cv.fromarray(search_array)
          
        #self.templates = list()      
        template = np.array(self.get_template(search_array, roi), dtype=np.uint8)
        self.templates.append(template)
        rospy.loginfo("Got Initial Template")
        
        try:
            if self.template_scale_and_rotate:
#                """ Compute the min and max scales """
#                width_ratio = float(search_array.shape[0]) / template.shape[0]
#                height_ratio = float(search_array.shape[1]) / template.shape[1]
#                
#                max_scale = 0.9 * min(width_ratio, height_ratio)
#                
#                max_template_dimension = max(template.shape[0], template.shape[1])
#                min_scale = 1.1 * float(self.template_min_size) / max_template_dimension
#                
#                self.scales = list()
#                scale = min_scale
#                while scale < max_scale:
#                    self.scales.append(scale)
#                    scale *= self.template_scale_factor
                                    
                self.rotations = [-25, 0, 25]
            else:
                self.scales = [1]
                self.rotations = [0]
                                    
            self.last_scale = 0 # index in self.scales
            self.last_rotation = 0
        except:
            rospy.loginfo("Exception generating scales and rotations!")
            return True   

        return True
    
    def load_face_detector(self):
        try:
            """ Set up the Haar face detection parameters """
            cascade_frontal_alt = rospy.get_param("~cascade_frontal_alt", "")
            cascade_frontal_alt2 = rospy.get_param("~cascade_frontal_alt2", "")
            cascade_profile = rospy.get_param("~cascade_profile", "")
            cascade_eye = rospy.get_param("~cascade_eye", "")
            
            self.cascade_frontal_alt = cv2.CascadeClassifier(cascade_frontal_alt)
            self.cascade_frontal_alt2 = cv2.CascadeClassifier(cascade_frontal_alt2)
            self.cascade_profile = cv2.CascadeClassifier(cascade_profile)
            self.cascade_eye = cv2.CascadeClassifier(cascade_eye)
            
            return True
        except:
            rospy.loginfo("Exception loading face detector!")
            return False
    
    def check_templates(self, cv_image, roi): 
        test_template = self.get_template(cv_image, roi)
        
        cv.NamedWindow("Test Template", cv.CV_NORMAL)
        #cv.ResizeWindow("Test Template", 320, 240)
        cv.MoveWindow("Test Template", 800, 50)
        cv.ShowImage("Test Template", test_template)
        
        test_array = np.array(test_template, dtype=np.uint8)
        test_template = cv.fromarray(cv2.resize(test_array, (self.templates[0].cols, self.templates[0].rows)))
        score = cv.DotProduct(test_template, self.templates[0]) / sqrt((cv.DotProduct(test_template, test_template) * cv.DotProduct(self.templates[0], self.templates[0])))
        
        #rospy.loginfo("SCORE: " + str(score))
    
    def get_template(self, cv_array, roi):
        cv_image = cv.fromarray(cv_array)
        try:
            (center, size, angle) = roi
            pt1 = (int(center[0] - size[0] / 2), int(center[1] - size[1] / 2))
            pt2 = (int(center[0] + size[0] / 2), int(center[1] + size[1] / 2))
            w = pt2[0] - pt1[0]
            h = pt2[1] - pt1[1]
        except:
            (x, y, w, h) = roi
            pt1 = (x, y)
            pt2 = (x + w, y + h)
            
        roi = (pt1[0], pt1[1], w, h)
        
        #try:    
        template = cv_image[pt1[1]:pt2[1],pt1[0]:pt2[0]]
        
        win_name = "Template_"+str(len(self.templates))

        template_array = np.array(template, dtype=np.uint8)
        cv2.imwrite(self.face_dir + "/" + win_name + ".png", template_array)

        cv.NamedWindow(win_name, cv.CV_NORMAL)
        cv.ResizeWindow(win_name, w * 2, h * 2)
        cv.MoveWindow(win_name, 700, 50*len(self.templates))
        cv.ShowImage(win_name, template)
        
        return template
#        except:
#            rospy.loginfo("Exception getting template!")
#            return None
        

    def load_face_detector(self):
        try:
            """ Set up the Haar face detection parameters """
            cascade_frontal_alt = rospy.get_param("~cascade_frontal_alt", "")
            cascade_frontal_alt2 = rospy.get_param("~cascade_frontal_alt2", "")
            cascade_profile = rospy.get_param("~cascade_profile", "")
            cascade_eye = rospy.get_param("~cascade_eye", "")
            
            self.cascade_frontal_alt = cv2.CascadeClassifier(cascade_frontal_alt)
            self.cascade_frontal_alt2 = cv2.CascadeClassifier(cascade_frontal_alt2)
            self.cascade_profile = cv2.CascadeClassifier(cascade_profile)
            self.cascade_eye = cv2.CascadeClassifier(cascade_eye)
            
            return True
        except:
            rospy.loginfo("Exception loading face detector!")
            return False
    
    def detect_face(self, cv_image):
        self.last_face_box = None
        if self.last_face_box is not None:
            self.search_scale = 1.5
            [x, y, w, h] = self.last_face_box
            w_new = int(self.search_scale * w)
            h_new = int(self.search_scale * h)
            search_box = (max(0, int(x - (w_new - w)/2)), max(0, int(y - (h_new - h)/2)), min(self.frame_size[0], w_new), min(self.frame_size[1], h_new))
            [sx, sy, sw, sh] = search_box
            pt1 = (sx, sy)
            pt2 = (x + sw, sy + sh)
            search_image = self.grey[pt1[1]:pt2[1], pt1[0]:pt2[0]]
            cv.Rectangle(self.marker_image, pt1, pt2, cv.RGB(0, 255, 0), 3)
        else:
            """ Reduce input image size for faster processing """
            search_image = cv2.resize(self.grey, (self.grey.shape[1] / self.haar_image_scale, self.grey.shape[0] / self.haar_image_scale))
                
        """ First check one of the frontal templates """
        faces = self.cascade_frontal_alt2.detectMultiScale(search_image, **self.haar_params)
                                         
        """ If that fails, check the profile template """
        if not len(faces):
            faces = self.cascade_profile.detectMultiScale(search_image, **self.haar_params)

        """ If that fails, check a different frontal profile """
        if not len(faces):
            faces = self.cascade_frontal_alt.detectMultiScale(search_image, **self.haar_params)

        if not len(faces):
            self.last_face_box = None
            if self.show_text:
                hscale = 0.4 * self.frame_size[0] / 160. + 0.1
                vscale = 0.4 * self.frame_size[1] / 120. + 0.1
                text_font = cv.InitFont(cv.CV_FONT_VECTOR0, hscale, vscale, 0, 1, 8)
                cv.PutText(self.marker_image, "LOST FACE!", (50, int(self.frame_size[1] * 0.9)), text_font, cv.RGB(255, 255, 0))
            return None
                
        for (x, y, w, h) in faces:
            """ The input to cv.HaarDetectObjects was resized, so scale the 
                bounding box of each face and convert it to two CvPoints """
            if self.last_face_box is not None:
                [s_x, s_y, s_w, s_h] = search_box
                pt1 = [x + s_x, y + s_y]
                pt2 = [pt1[0] + w, pt1[1] + h]
                [face_width, face_height] = w, h
                self.last_face_box = None
            else:
                pt1 = [int(x * self.haar_image_scale), int(y * self.haar_image_scale)]
                pt2 = [int((x + w) * self.haar_image_scale), int((y + h) * self.haar_image_scale)]
                face_width = pt2[0] - pt1[0]
                face_height = pt2[1] - pt1[1]

            if self.use_depth_for_detection:
                """ Get the average distance over the face box """
                ave_face_distance = 0
                i = 0
                for x in range(pt1[0], pt2[0]):
                    for y in range(pt1[1], pt2[1]):
                        try:
                            face_distance = cv.Get2D(self.depth_image, y, x)
                            z = face_distance[0]
                        except:
                            continue
                        if isnan(z):
                            continue
                        else:
                            ave_face_distance += z
                            i = i + 1

                """ If we are too close to the Kinect, we will get NaN for distances so just accept the detection. """
                if i == 0:
                    face_size = 0
                
                else:
                    """ Compute the size of the face in meters (average of width and height)
                        The Kinect's FOV is about 57 degrees wide which is, coincidentally, about 1 radian.
                    """
                    ave_face_distance = ave_face_distance / float(i)
                    arc = (self.fov_width * float(face_width) / float(self.frame_size[0]) + self.fov_height * float(face_height) / float(self.frame_size[1])) / 2.0
                    face_size = ave_face_distance * arc
                
                if face_size > self.max_face_size:
                    continue
            
            self.face_scale_factor = 1.0
            new_face_width = int(face_width * self.face_scale_factor)
            new_face_height = int(face_height * self.face_scale_factor)
            pt1[0] = max(0, int(pt1[0] - face_width * (self.face_scale_factor - 1) / 2))
            pt1[1] = max(0, int(pt1[1] - face_height * (self.face_scale_factor - 1) / 2))
            pt2[0] = min(self.frame_size[0], int(pt1[0] + new_face_width))
            pt2[1] = min(self.frame_size[1], int(pt1[1] + new_face_height))

            face_box = (pt1[0], pt1[1], new_face_width, new_face_height)
            pt1 = (pt1[0], pt1[1])
            pt2 = (pt2[0], pt2[1])

            cv.Rectangle(self.marker_image, pt1, pt2, cv.RGB(0, 255, 0), 3)
            
            if face_box is not None:
                self.ROI = RegionOfInterest()
                self.ROI.x_offset = min(self.frame_size[0], max(0, pt1[0]))
                self.ROI.y_offset = min(self.frame_size[1], max(0, pt2[0]))
                self.ROI.width = min(self.frame_size[0], face_width)
                self.ROI.height = min(self.frame_size[1], face_height)
                
            self.pubROI.publish(self.ROI)
            
            self.last_face_box = face_box

            """ Break out of the loop after the first face """
            return face_box

    def get_keypoints(self, track_box):
        """ Zero the mask with all black pixels """
        self.mask = np.zeros_like(self.grey)
 
        """ Get the coordinates and dimensions of the current track box """
        try:
            ((x,y), (w,h), a) = track_box
        except:
            try:
                x,y,w,h = track_box
            except:
                rospy.loginfo("Track box has shrunk to zero...")
                return
        
        """ Set the rectangule within the mask to white """
        self.mask[y:y+h, x:x+w] = 255
        
        if self.keypoints is not None:
            for x, y in [np.int32(p) for p in self.keypoints]:
                cv2.circle(mask, (x, y), 5, 0, -1)

        # Get the new keypoints using Good Features to Track
        if self.keypoint_type == 0:
            corners = cv2.goodFeaturesToTrack(self.grey, mask = self.mask, **self.gf_params)
            if corners is not None:
                for x, y in np.float32(corners).reshape(-1, 2):
                    self.keypoints.append((x, y))
                    
        # Get the new keypoints using SURF
        elif self.keypoint_type == 1:
            surf_keypoints, surf_descriptors = self.surf.detect(self.grey, self.mask, False)
            for keypoint in surf_keypoints:
                self.keypoints.append((int(keypoint.pt[0]), int(keypoint.pt[1])))

        if self.show_features:
            for x, y in self.keypoints:
                cv.Circle(self.marker_image, (x, y), self.feature_size, (0, 255, 0, 0), cv.CV_FILLED, 8, 0)
                
        if self.auto_min_keypoints:
            """ Since the detect box is larger than the actual face or desired patch, shrink the number of features by 10% """
            self.min_keypoints = int(len(self.keypoints) * 0.9)
            self.abs_min_keypoints = int(0.5 * self.min_keypoints)  
                    
    def track_keypoints(self):
        if len(self.keypoints) > 0:
            img0, img1 = self.prev_grey, self.grey
            p0 = np.float32([p for p in self.keypoints]).reshape(-1, 1, 2)
            p1, good, err = cv2.calcOpticalFlowPyrLK(img0, img1, p0, None, **self.lk_params)
            #p0r, st, err = cv2.calcOpticalFlowPyrLK(img1, img0, p1, None, **self.lk_params)
            #d = abs(p0-p0r).reshape(-1, 2).max(-1)
            #good = d < 1
            new_keypoints = []
            for p, (x, y), good in zip(self.keypoints, p1.reshape(-1, 2), good):
                if not good:
                    continue
                new_keypoints.append((x, y))
                cv.Circle(self.marker_image, (x, y), 1, (0, 255, 0, 0), cv.CV_FILLED, 2, 0)
            self.keypoints = new_keypoints
            
        """ Draw the best fit ellipse around the feature points """
        if len(self.keypoints) > 6:
            self.keypoints_matrix = cv.CreateMat(1, len(self.keypoints), cv.CV_32SC2)
            i = 0
            for p in self.keypoints:
                cv.Set2D(self.keypoints_matrix, 0, i, (int(p[0]), int(p[1])))
                i = i + 1           
            keypoints_box = cv.FitEllipse2(self.keypoints_matrix)
            #keypoints_box = cv.MinAreaRect2(self.keypoints_matrix)
        else:
            keypoints_box = None
            
        """ Publish the ROI for the tracked object """
        try:
            (roi_center, roi_size, roi_angle) = keypoints_box
        except:
            rospy.loginfo("Track box has shrunk to zero...")
            keypoints_box = None
            
        if keypoints_box and not self.drag_start and self.is_rect_nonzero(self.track_box):
            self.ROI = RegionOfInterest()
            self.ROI.x_offset = min(self.frame_size[0], max(0, int(roi_center[0] - roi_size[0] / 2)))
            self.ROI.y_offset = min(self.frame_size[1], max(0, int(roi_center[1] - roi_size[1] / 2)))
            self.ROI.width = min(self.frame_size[0], int(roi_size[0]))
            self.ROI.height = min(self.frame_size[1], int(roi_size[1]))
            
        self.pubROI.publish(self.ROI)
        
#        """ If using depth info Publish the centroid of the tracked cluster as a PointStamped message """
#        if self.use_depth_for_detection or self.use_depth_for_tracking:
#            if keypoints_box is not None and not self.drag_start and self.is_rect_nonzero(self.track_box):
#                self.cluster3d.header.frame_id = self.camera_frame_id
#                self.cluster3d.header.stamp = rospy.Time()
#                self.cluster3d.point.x = self.cog_x
#                self.cluster3d.point.y = self.cog_y
#                self.cluster3d.point.z = self.cog_z
#                self.pub_cluster3d.publish(self.cluster3d)
            
        return keypoints_box
    
    def add_keypoints(self, track_box):
        """ Look for any new keypoints around the current feature cloud """
        
        """ Begin with a mask of all black pixels """
        mask = np.zeros_like(self.grey)
        
        """ Get the coordinates and dimensions of the current track box """
        try:
            ((x,y), (w,h), a) = track_box
        except:
            try:
                x,y,w,h = track_box
            except:
                rospy.loginfo("Track box has shrunk to zero...")
                return
        
        x = int(x)
        y = int(y)
        
        """ Expand the track box to look for new keypoints """
        w_new = int(self.expand_roi * w)
        h_new = int(self.expand_roi * h)
        
        pt1 = (x - int(w_new / 2), y - int(h_new / 2))
        pt2 = (x + int(w_new / 2), y + int(h_new / 2))

        if self.show_add_drop:
            cv.Rectangle(self.marker_image, pt1, pt2, cv.RGB(255, 255, 0))
        
        mask_box = ((x, y), (w_new, h_new), a)

        # Rectangular mask
        #mask[y:y+h_new,x:x+w_new] = 255
                        
        """ Create a filled white ellipse within the track_box to define the ROI. """
        cv2.ellipse(mask, mask_box, cv.CV_RGB(255,255, 255), cv.CV_FILLED)

        if self.keypoints is not None:
            # Mask the current keypoints
            for x, y in [np.int32(p) for p in self.keypoints]:
                cv2.circle(mask, (x, y), 5, 0, -1)

        if self.keypoint_type == 0:
            """ Get the new keypoints using Good Features to Track """
            new_keypoints = cv2.goodFeaturesToTrack(self.grey, mask = mask, **self.gf_params)

        elif self.keypoint_type == 1:
            """ Get the new keypoints using SURF """
            new_keypoints = []
            surf_keypoints, surf_descriptors = self.surf.detect(self.grey, self.mask, False)
            for keypoint in surf_keypoints:
                new_keypoints.append((int(keypoint.pt[0]), int(keypoint.pt[1])))

        """ Append new keypoints to the current list if they are not too far from the current cluster """          
        if new_keypoints is not None:
            for x, y in np.float32(new_keypoints).reshape(-1, 2):
                distance = self.distance_to_cluster((x,y), self.keypoints)
                if distance > self.add_keypoint_distance:
                    self.keypoints.append((x,y))
                    if self.show_add_drop:
                        cv.Circle(self.marker_image, (x, y), 3, (255, 255, 0, 0), cv.CV_FILLED, 2, 0)
                                    
            """ Remove duplicate keypoints """
            self.keypoints = list(set(self.keypoints))
        
    def distance_to_cluster(self, test_point, cluster):
        min_distance = 10000
        for point in cluster:
            if point == test_point:
                continue
            """ Use L1 distance since it is faster than L2 """
            distance = abs(test_point[0] - point[0])  + abs(test_point[1] - point[1])
            if distance < min_distance:
                min_distance = distance
        return min_distance
    
    def drop_keypoints(self, min_keypoints, outlier_threshold, mse_threshold):
        sum_x = 0
        sum_y = 0
        sum_z = 0
        sse = 0
        keypoints_xy = self.keypoints
        keypoints_z = self.keypoints
        n_xy = len(self.keypoints)
        n_z = n_xy
        
        if self.use_depth_for_tracking:
            if not self.depth_image:
                return ((0, 0, 0), 0, 0, -1)
            else:
                (cols, rows) = cv.GetSize(self.depth_image)
        
        """ If there are no keypoints left to track, start over """
        if n_xy == 0:
            return ((0, 0, 0), 0, 0, -1)
        
        """ Compute the COG (center of gravity) of the cluster """
        for point in self.keypoints:
            sum_x = sum_x + point[0]
            sum_y = sum_y + point[1]
        
        mean_x = sum_x / n_xy
        mean_y = sum_y / n_xy
        
        if self.use_depth_for_tracking:
            for point in self.keypoints:
                try:
                    z = cv.Get2D(self.depth_image, min(rows - 1, int(point[1])), min(cols - 1, int(point[0])))
                except:
                    continue
                z = z[0]
                """ Depth values can be NaN which should be ignored """
                if isnan(z):
                    continue
                else:
                    sum_z = sum_z + z
                    
            mean_z = sum_z / n_z
            
        else:
            mean_z = -1
        
        """ Compute the x-y MSE (mean squared error) of the cluster in the camera plane """
        for point in self.keypoints:
            sse = sse + (point[0] - mean_x) * (point[0] - mean_x) + (point[1] - mean_y) * (point[1] - mean_y)
            #sse = sse + abs((point[0] - mean_x)) + abs((point[1] - mean_y))
        
        """ Get the average over the number of feature points """
        mse_xy = sse / n_xy
        
        """ The MSE must be > 0 for any sensible feature cluster """
        if mse_xy == 0 or mse_xy > mse_threshold:
            return ((0, 0, 0), 0, 0, -1)
        
        """ Throw away the outliers based on the x-y variance """
        max_err = 0
        for point in self.keypoints:
            std_err = ((point[0] - mean_x) * (point[0] - mean_x) + (point[1] - mean_y) * (point[1] - mean_y)) / mse_xy
            if std_err > max_err:
                max_err = std_err
            if std_err > outlier_threshold:
                keypoints_xy.remove(point)
                if self.show_add_drop:
                    # Briefly mark the removed points in red
                    cv.Circle(self.marker_image, (point[0], point[1]), 2, (0, 0, 255), cv.CV_FILLED)   
                try:
                    keypoints_z.remove(point)
                    n_z = n_z - 1
                except:
                    pass
                
                n_xy = n_xy - 1
                                
        """ Now do the same for depth """
        if self.use_depth_for_tracking:
            sse = 0
            for point in keypoints_z:
                try:
                    z = cv.Get2D(self.depth_image, min(rows - 1, int(point[1])), min(cols - 1, int(point[0])))
                    z = z[0]
                    sse = sse + (z - mean_z) * (z - mean_z)
                except:
                    n_z = n_z - 1
            
            mse_z = sse / n_z
            
            """ Throw away the outliers based on depth using percent error rather than standard error since depth
                 values can jump dramatically at object boundaries  """
            for point in keypoints_z:
                try:
                    z = cv.Get2D(self.depth_image, min(rows - 1, int(point[1])), min(cols - 1, int(point[0])))
                    z = z[0]
                except:
                    continue
                try:
                    pct_err = abs(z - mean_z) / mean_z
                    if pct_err > self.pct_err_z:
                        keypoints_xy.remove(point)
                except:
                    pass
        else:
            mse_z = -1
        
        self.keypoints = keypoints_xy
               
        """ Consider a cluster bad if we have fewer than min_keypoints left """
        if len(self.keypoints) < min_keypoints:
            score = -1
        else:
            score = 1

        return ((mean_x, mean_y, mean_z), mse_xy, mse_z, score)
    
def mkdir_p(path):
    try:
        os.makedirs(path)
    except OSError as exc: # Python >2.5
        if exc.errno == errno.EEXIST:
            pass
        else: raise
        
    
def main(args):
      AFT = AdaptiveFaceTracker("adaptive_face_tracker")
      try:
        rospy.spin()
      except KeyboardInterrupt:
        print "Shutting down face tracker node."
        cv.DestroyAllWindows()

if __name__ == '__main__':
    main(sys.argv)
    