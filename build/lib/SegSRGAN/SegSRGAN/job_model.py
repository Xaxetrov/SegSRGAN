# -*- coding: utf-8 -*-
import Function_for_application_test_python3
import pandas as pd
import glob
import os
import argparse
import ast

import numpy as np

parser = argparse.ArgumentParser()
parser.add_argument("-p", "--path", type=str, help="Path of the csv file")
parser.add_argument("-dp", "--debut_path", type=str, help="Path beginning of the csv (default: %(default)s voxels)",
                    default='')
parser.add_argument("-pa", "--patch", type=str, help="Patch size (default: %(default)s)", default=128)
parser.add_argument("-s", "--step", type=str, help="Step between patches. Must be a tuple of tuple (default: "
                                                   "%(default)s)", default=64)
parser.add_argument("-rf", "--result_folder_name", type=str, help='Name of the folder where the result is going to be '
                                                                  'stored')
parser.add_argument("-wp", "--weights_relative_path", type=str, help='Weights relative paths')
parser.add_argument("-bb", "--by_batch", type=str, help="Prediction on list of patches instead of using a for loop. "
                                                         "Enables for instance to automatically computes in multi-gpu "
                                                         "mode(default: %(default)s)", default="False")
parser.add_argument("-ic", "--is_conditional", type=str, help="Enable conditional prediction on z resolution",
                    default="False")
args = parser.parse_args()

by_batch = ast.literal_eval(args.by_batch)
is_conditional = ast.literal_eval(args.is_conditional)
# Argument :
# name of the result folder
result_folder = args.result_folder_name

weights_relative_path = args.weights_relative_path

debut_relatif_path = args.debut_path  # Path to Base_pour_romeo

data = pd.read_csv(debut_relatif_path + args.path, header=None).iloc[:, 0]

path_pour_application = debut_relatif_path + data

path_pour_application = path_pour_application.sort_values()


def list_of_lists(arg):
    m = [x.split(' ') for x in arg.split(',')]
    m = np.array(m).astype('int')
    return m.tolist()


def list_of(arg):
    m = [x for x in arg.split(',')]
    m = np.array(m).astype('int')
    return m.tolist()


ensemble_pas = list_of_lists(args.step)
patchs = list_of(args.patch)

# Function :


def create_folder(directory):
    try:
        if not os.path.exists(directory):
            os.makedirs(directory)
    except OSError:
        print('Error: Creating directory. ' + directory)

# Execution :


ensemble_pas = pd.DataFrame(ensemble_pas, index=patchs)

np_pas_per_patch = ensemble_pas.shape[1]

weights_path = os.getcwd() + weights_relative_path

for i in path_pour_application:

    for patch in patchs:

        if patch is None:

            i_split = i.split("/")

            path_output = "/".join(i_split[:(len(i_split) - 1)]) + "/code avec " + result_folder + "/patch " + str(
                patch)

            print(path_output)

            if not os.path.exists(path_output):

                create_folder(path_output)

                path_output_cortex = path_output + "/Cortex " + str(patch) + ".nii.gz"

                path_output_SR = path_output + "/SR " + str(patch) + ".nii.gz"

                Function_for_application_test_python3.segmentation(input_file_path=i,
                                                                   step=20,
                                                                   NewResolution=(0.5, 0.5, 0.5),
                                                                   patch=patch,
                                                                   path_output_cortex=path_output_cortex,
                                                                   path_output_HR=path_output_SR,
                                                                   weights_path=weights_path,
                                                                   by_batch=by_batch,
                                                                   is_conditional=is_conditional
                                                                   )
            else:

                print("already computed")
        else:

            for step in ensemble_pas.loc[patch]:

                i_split = i.split("/")

                path_output = "/".join(i_split[:(len(i_split) - 1)]) + "/code avec " + result_folder + "/patch " + str(
                    patch) + " step " + str(step) + " inversion shave padd"

                print(path_output)

                if not os.path.exists(path_output):

                    create_folder(path_output)

                    path_output_cortex = path_output + "/Cortex patch " + str(patch) + " step " + str(step) + ".nii.gz"

                    path_output_SR = path_output + "/SR.nii.gz" + str(patch) + " step " + str(step) + ".nii.gz"

                    Function_for_application_test_python3.segmentation(input_file_path=i,
                                                                       step=step,
                                                                       NewResolution=(0.5, 0.5, 0.5),
                                                                       patch=patch,
                                                                       path_output_cortex=path_output_cortex,
                                                                       path_output_HR=path_output_SR,
                                                                       weights_path=weights_path,
                                                                       by_batch=by_batch,
                                                                       is_conditional=is_conditional
                                                                       )
                else:

                    print("already computed")
