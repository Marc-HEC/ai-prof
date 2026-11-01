// Animations Mixamo (.fbx) jouées sur un VRM : on retarget le squelette Mixamo sur les os
// humanoïdes du VRM. Adapté de l'exemple officiel de three-vrm (loadMixamoAnimation.js, MIT).
// Sur mixamo.com : choisis l'animation, « Download », format FBX, « Without Skin ».
// Coche « In Place » pour la marche, sinon l'avatar sort du cadre.
import * as THREE from "three";
import { FBXLoader } from "three/addons/loaders/FBXLoader.js";

const RIG = {
  mixamorigHips: "hips", mixamorigSpine: "spine", mixamorigSpine1: "chest", mixamorigSpine2: "upperChest",
  mixamorigNeck: "neck", mixamorigHead: "head",
  mixamorigLeftShoulder: "leftShoulder", mixamorigLeftArm: "leftUpperArm", mixamorigLeftForeArm: "leftLowerArm",
  mixamorigLeftHand: "leftHand",
  mixamorigLeftHandThumb1: "leftThumbMetacarpal", mixamorigLeftHandThumb2: "leftThumbProximal", mixamorigLeftHandThumb3: "leftThumbDistal",
  mixamorigLeftHandIndex1: "leftIndexProximal", mixamorigLeftHandIndex2: "leftIndexIntermediate", mixamorigLeftHandIndex3: "leftIndexDistal",
  mixamorigLeftHandMiddle1: "leftMiddleProximal", mixamorigLeftHandMiddle2: "leftMiddleIntermediate", mixamorigLeftHandMiddle3: "leftMiddleDistal",
  mixamorigLeftHandRing1: "leftRingProximal", mixamorigLeftHandRing2: "leftRingIntermediate", mixamorigLeftHandRing3: "leftRingDistal",
  mixamorigLeftHandPinky1: "leftLittleProximal", mixamorigLeftHandPinky2: "leftLittleIntermediate", mixamorigLeftHandPinky3: "leftLittleDistal",
  mixamorigRightShoulder: "rightShoulder", mixamorigRightArm: "rightUpperArm", mixamorigRightForeArm: "rightLowerArm",
  mixamorigRightHand: "rightHand",
  mixamorigRightHandThumb1: "rightThumbMetacarpal", mixamorigRightHandThumb2: "rightThumbProximal", mixamorigRightHandThumb3: "rightThumbDistal",
  mixamorigRightHandIndex1: "rightIndexProximal", mixamorigRightHandIndex2: "rightIndexIntermediate", mixamorigRightHandIndex3: "rightIndexDistal",
  mixamorigRightHandMiddle1: "rightMiddleProximal", mixamorigRightHandMiddle2: "rightMiddleIntermediate", mixamorigRightHandMiddle3: "rightMiddleDistal",
  mixamorigRightHandRing1: "rightRingProximal", mixamorigRightHandRing2: "rightRingIntermediate", mixamorigRightHandRing3: "rightRingDistal",
  mixamorigRightHandPinky1: "rightLittleProximal", mixamorigRightHandPinky2: "rightLittleIntermediate", mixamorigRightHandPinky3: "rightLittleDistal",
  mixamorigLeftUpLeg: "leftUpperLeg", mixamorigLeftLeg: "leftLowerLeg", mixamorigLeftFoot: "leftFoot", mixamorigLeftToeBase: "leftToes",
  mixamorigRightUpLeg: "rightUpperLeg", mixamorigRightLeg: "rightLowerLeg", mixamorigRightFoot: "rightFoot", mixamorigRightToeBase: "rightToes",
};

const loader = new FBXLoader();

export async function loadMixamoClip(url, vrm) {
  const asset = await loader.loadAsync(url);
  const clip = THREE.AnimationClip.findByName(asset.animations, "mixamo.com") ?? asset.animations[0];
  if (!clip) return null;
  const tracks = [];
  const restInverse = new THREE.Quaternion(), parentRest = new THREE.Quaternion(), q = new THREE.Quaternion();
  const v = new THREE.Vector3();
  // Mise à l'échelle des déplacements du bassin : le squelette Mixamo est en centimètres.
  const motionHips = asset.getObjectByName("mixamorigHips")?.position.y || 1;
  const vrmHips = Math.abs(vrm.humanoid.getNormalizedBoneNode("hips").getWorldPosition(v).y - vrm.scene.getWorldPosition(v).y);
  const scale = vrmHips / motionHips;
  const vrm0 = vrm.meta?.metaVersion === "0";

  for (const track of clip.tracks) {
    const [rigName, property] = track.name.split(".");
    const node = vrm.humanoid.getNormalizedBoneNode(RIG[rigName])?.name;
    const rigNode = asset.getObjectByName(rigName);
    if (!node || !rigNode) continue;
    rigNode.getWorldQuaternion(restInverse).invert();
    rigNode.parent.getWorldQuaternion(parentRest);
    if (track instanceof THREE.QuaternionKeyframeTrack) {
      const values = track.values.slice();
      for (let i = 0; i < values.length; i += 4) {
        q.fromArray(values, i).premultiply(parentRest).multiply(restInverse).toArray(values, i);
      }
      tracks.push(new THREE.QuaternionKeyframeTrack(`${node}.${property}`, track.times,
        values.map((x, i) => (vrm0 && i % 2 === 0 ? -x : x))));
    } else if (track instanceof THREE.VectorKeyframeTrack && property === "position") {
      tracks.push(new THREE.VectorKeyframeTrack(`${node}.${property}`, track.times,
        track.values.map((x, i) => (vrm0 && i % 3 !== 1 ? -x : x) * scale)));
    }
  }
  return new THREE.AnimationClip(clip.name, clip.duration, tracks);
}
