import * as THREE from '../vendored/three/three.module.js';
import { ARTIFACT_TEMPLATES } from './landing-icons.js';

const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');

function mountLandingScene() {
    let frame = 0;
    let contextLost = false;
    let scene, camera, renderer, raycaster, mouse, invisiblePlane;
    let heartParticles, heartGeometry, heartBasePositions, heartBaseColors;
    let activeConstellations = [];
    let lastSpawnTime = 0;
    let lastSpawnPos = new THREE.Vector3(999, 999, 999);
    let clock = new THREE.Clock();
    let scrollProgress = 0;
    function createStarTexture() {
        const canvas = document.createElement('canvas');
        canvas.width = 64;
        canvas.height = 64;
        const ctx = canvas.getContext('2d');

        const grad = ctx.createRadialGradient(32, 32, 0, 32, 32, 30);
        grad.addColorStop(0, 'rgba(255, 255, 255, 1)');
        grad.addColorStop(0.16, 'rgba(253, 244, 255, 0.92)');
        grad.addColorStop(0.38, 'rgba(217, 70, 239, 0.38)');
        grad.addColorStop(0.7, 'rgba(139, 92, 246, 0.12)');
        grad.addColorStop(1, 'rgba(0, 0, 0, 0)');

        ctx.fillStyle = grad;
        ctx.fillRect(0, 0, 64, 64);

        return new THREE.CanvasTexture(canvas);
    }

    let starTexture;

    function initThreeScene() {
        const container = document.getElementById('canvas-container');
        scene = new THREE.Scene();
        scene.fog = new THREE.FogExp2(0x070919, 0.011);

        camera = new THREE.PerspectiveCamera(52, window.innerWidth / window.innerHeight, 0.1, 200);
        camera.position.set(0, 10.2, 23.0);
        camera.lookAt(0, 0.2, 0);

        try {
            renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: 'high-performance' });
        } catch (error) {
            console.warn('Landing particle effects unavailable; retaining the static background.', error);
            return;
        }
        renderer.setSize(window.innerWidth, window.innerHeight);
        renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
        renderer.domElement.id = 'landing-stars';
        container.appendChild(renderer.domElement);

        starTexture = createStarTexture();
        raycaster = new THREE.Raycaster();
        mouse = new THREE.Vector2(-99, -99);
        const heartGroup = new THREE.Group();
        heartGroup.position.set(0, -2.2, 1.8);
        heartGroup.rotation.x = -0.24;
        scene.add(heartGroup);
        const planeGeo = new THREE.PlaneGeometry(110, 95);
        planeGeo.rotateX(-Math.PI / 2);
        const planeMat = new THREE.MeshBasicMaterial({ visible: false });
        invisiblePlane = new THREE.Mesh(planeGeo, planeMat);
        heartGroup.add(invisiblePlane);
        buildHeartParticleField(heartGroup);
        buildAstrolabeGuides(heartGroup);
        window.addEventListener('resize', onWindowResize);
        window.addEventListener('pointermove', onPointerMove, { passive: true });
        document.documentElement.addEventListener('pointerleave', () => { currentHover3D = null; });
        window.addEventListener('click', onSceneClick);
        window.addEventListener('scroll', onWindowScroll, { passive: true });

        onWindowScroll();

        setTimeout(() => {
            if (reducedMotion.matches || document.hidden || contextLost) return;
            spawnConstellationAt(new THREE.Vector3(-7.8, -2.2, 2.5), 0);
            spawnConstellationAt(new THREE.Vector3(7.8, -2.0, 2.2), 6);
        }, 500);

        mountArtifactControls();
        document.addEventListener('visibilitychange', updateAnimation);
        reducedMotion.addEventListener('change', updateAnimation);
        renderer.domElement.addEventListener('webglcontextlost', event => {
            event.preventDefault();
            contextLost = true;
            updateAnimation();
        });
        renderer.domElement.addEventListener('webglcontextrestored', () => {
            contextLost = false;
            updateAnimation();
        });
        updateAnimation();
    }

    function onWindowScroll() {
        if (reducedMotion.matches) return;
        const maxScroll = Math.max(1, document.documentElement.scrollHeight - window.innerHeight);
        scrollProgress = THREE.MathUtils.clamp(window.scrollY / maxScroll, 0, 1);
        const ringInner = document.getElementById('ring-inner');
        const ringOuter = document.getElementById('ring-outer');
        if (ringInner && ringOuter) {
            const topPct = 72 - scrollProgress * 28;
            document.querySelector('.landing-sky').style.setProperty('--ring-top', `${topPct}%`);
        }
    }

    function buildHeartParticleField(parentGroup) {
        const count = 16500;
        heartGeometry = new THREE.BufferGeometry();
        const positions = new Float32Array(count * 3);
        heartBasePositions = new Float32Array(count * 3);
        heartBaseColors = new Float32Array(count * 3);
        const colors = new Float32Array(count * 3);
        const colIndigo   = new THREE.Color('#4C1D95');
        const colElectric = new THREE.Color('#9333EA');
        const colMagenta  = new THREE.Color('#D946EF');
        const colCoral    = new THREE.Color('#DB4D96');
        const colWarmPink = new THREE.Color('#E67BB6');
        const colStarCore = new THREE.Color('#FDF4FF');

        for (let i = 0; i < count; i++) {
            const t = Math.random() * Math.PI * 2;
            const isEdgeParticle = i < count * 0.22;
            const r = isEdgeParticle
                ? 0.86 + Math.random() * 0.14
                : Math.pow(Math.random(), 0.48);
            const scale = 1.51;

            const hx = 16 * Math.pow(Math.sin(t), 3);
            const hz = -(13 * Math.cos(t) - 5 * Math.cos(2 * t) - 2 * Math.cos(3 * t) - Math.cos(4 * t));

            const jitterAmt = isEdgeParticle ? 0.28 : 0.55;
            const jitterX = (Math.random() - 0.5) * jitterAmt;
            const jitterZ = (Math.random() - 0.5) * jitterAmt;

            const x = hx * r * scale + jitterX;
            const z = hz * r * scale + jitterZ;
            const y = (Math.random() - 0.5) * 0.42 + Math.cos(r * Math.PI) * 0.58;

            positions[i * 3] = x;
            positions[i * 3 + 1] = y;
            positions[i * 3 + 2] = z;

            heartBasePositions[i * 3] = x;
            heartBasePositions[i * 3 + 1] = y;
            heartBasePositions[i * 3 + 2] = z;
            const mixX = THREE.MathUtils.clamp((x + 24) / 48, 0, 1);
            const finalColor = new THREE.Color();

            if (Math.random() < 0.075) {
                finalColor.copy(colStarCore);
            } else {
                finalColor.lerpColors(colElectric, colMagenta, mixX);
                finalColor.lerp(colCoral, Math.pow(mixX, 1.4) * 0.65);
                if (r > 0.84) {
                    finalColor.lerp(colWarmPink, 0.35);
                } else if (r < 0.35) {
                    finalColor.lerp(colMagenta, 0.4);
                } else {
                    finalColor.lerp(colIndigo, 0.25 * Math.random());
                }
            }
            const edgeBoost = r > 0.84 ? 0.14 : 0.0;
            const baseBrightness = 0.50 + Math.random() * 0.16 + edgeBoost;
            heartBaseColors[i * 3] = finalColor.r;
            heartBaseColors[i * 3 + 1] = finalColor.g;
            heartBaseColors[i * 3 + 2] = finalColor.b;

            colors[i * 3] = finalColor.r * baseBrightness;
            colors[i * 3 + 1] = finalColor.g * baseBrightness;
            colors[i * 3 + 2] = finalColor.b * baseBrightness;
        }

        heartGeometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
        heartGeometry.setAttribute('color', new THREE.BufferAttribute(colors, 3));

        const material = new THREE.PointsMaterial({
            size: 0.21,
            map: starTexture,
            vertexColors: true,
            transparent: true,
            opacity: 0.95,
            depthWrite: false,
            blending: THREE.AdditiveBlending
        });

        heartParticles = new THREE.Points(heartGeometry, material);
        parentGroup.add(heartParticles);
    }

    function buildAstrolabeGuides(parentGroup) {
        const ringMaterial = new THREE.LineBasicMaterial({
            color: 0xAD63D6,
            transparent: true,
            opacity: 0.08,
            blending: THREE.AdditiveBlending
        });

        [14, 22, 30].forEach(radius => {
            const pts = [];
            const segments = 120;
            for (let i = 0; i <= segments; i++) {
                const theta = (i / segments) * Math.PI * 2;
                pts.push(new THREE.Vector3(Math.cos(theta) * radius, -0.2, Math.sin(theta) * radius));
            }
            const geo = new THREE.BufferGeometry().setFromPoints(pts);
            const ring = new THREE.Line(geo, ringMaterial);
            parentGroup.add(ring);
        });
    }

    let artifactCounter = 0;

    function spawnConstellationAt(worldPos, forcedIndex = null) {
        if (reducedMotion.matches || document.hidden || contextLost) return;
        if (activeConstellations.length >= 7) {
            const oldest = activeConstellations.shift();
            removeConstellation(oldest);
        }

        const templateIndex = forcedIndex !== null
            ? forcedIndex
            : (artifactCounter++) % ARTIFACT_TEMPLATES.length;
        const template = ARTIFACT_TEMPLATES[templateIndex];

        const group = new THREE.Group();
        group.position.copy(worldPos);
        scene.add(group);

        const scale = 0.72;
        const riseHeight = 3.1 + Math.random() * 0.85;

        const targetCoords = [];
        const startCoords = [];
        const swirlSeeds = [];
        const starColors = [];

        const cCoreWhite = new THREE.Color('#FFFFFF');
        const cMagenta   = new THREE.Color('#E879F9');
        const cViolet    = new THREE.Color('#A855F7');
        const cCoral     = new THREE.Color('#E67BB6');

        function registerParticle(tx, ty, tz, col) {
            targetCoords.push(tx, ty, tz);
            const angle = Math.random() * Math.PI * 2;
            const rad = 0.35 + Math.random() * 1.95;
            startCoords.push(
                Math.cos(angle) * rad,
                -riseHeight + (Math.random() - 0.5) * 0.28,
                Math.sin(angle) * rad
            );
            swirlSeeds.push((Math.random() - 0.5) * 2.2);
            starColors.push(col.r, col.g, col.b);
        }
        template.anchors.forEach(([ax, ay]) => {
            registerParticle(ax * scale, ay * scale, 0, cCoreWhite);
        });
        const softSegmentPairs = [];

        template.strokes.forEach((strokePts, sIdx) => {
            for (let i = 0; i < strokePts.length; i++) {
                const [px, py] = strokePts[i];
                const ratio = i / Math.max(1, strokePts.length - 1);
                const ptColor = (sIdx % 2 === 0)
                    ? cMagenta.clone().lerp(cCoral, ratio)
                    : cViolet.clone().lerp(cMagenta, ratio);
                const jx = (Math.random() - 0.5) * 0.018;
                const jy = (Math.random() - 0.5) * 0.018;
                const pIndex = targetCoords.length / 3;
                registerParticle(px * scale + jx, py * scale + jy, 0, ptColor);
                if (i > 0 && i % 2 === 1) {
                    softSegmentPairs.push([pIndex - 1, pIndex]);
                }
            }
        });

        const pointsGeo = new THREE.BufferGeometry();
        const currentPositions = new Float32Array(startCoords);
        pointsGeo.setAttribute('position', new THREE.BufferAttribute(currentPositions, 3));
        pointsGeo.setAttribute('color', new THREE.Float32BufferAttribute(starColors, 3));

        const pointsMat = new THREE.PointsMaterial({
            size: 0.18,
            map: starTexture,
            vertexColors: true,
            transparent: true,
            opacity: 1,
            depthWrite: false,
            blending: THREE.AdditiveBlending
        });
        const starCloud = new THREE.Points(pointsGeo, pointsMat);
        group.add(starCloud);
        const lineVertices = new Float32Array(softSegmentPairs.length * 6);
        const lineGeo = new THREE.BufferGeometry();
        lineGeo.setAttribute('position', new THREE.BufferAttribute(lineVertices, 3));
        const lineMat = new THREE.LineBasicMaterial({
            color: 0xE879F9,
            transparent: true,
            opacity: 0.0,
            depthWrite: false,
            blending: THREE.AdditiveBlending
        });
        const constellationLines = new THREE.LineSegments(lineGeo, lineMat);
        group.add(constellationLines);
        group.position.y += riseHeight;

        activeConstellations.push({
            group,
            pointsGeo,
            pointsMat,
            lineGeo,
            lineMat,
            softSegmentPairs,
            template,
            scale,
            startCoords,
            targetCoords,
            swirlSeeds,
            baseY: group.position.y,
            age: 0,
            maxLife: 4.6
        });
    }

    function removeConstellation(item) {
        scene.remove(item.group);
        item.group.traverse(obj => {
            if (obj.geometry) obj.geometry.dispose();
            if (obj.material) obj.material.dispose();
        });
    }

    function spawnSpecificArtifact(index) {
        const angle = (Math.random() - 0.5) * 2.2;
        const x = Math.sin(angle) * (4.5 + Math.random() * 5.5);
        const zOffset = scrollProgress * 12.0;
        const z = (Math.random() - 0.5) * 4.5 + 1.0 + zOffset;
        spawnConstellationAt(new THREE.Vector3(x, -2.2 - scrollProgress * 2.2, z), index);
    }


    function triggerAllShowcase() {
        const zShift = scrollProgress * 11.5;
        const yShift = -scrollProgress * 2.2;
        const positions = [
            new THREE.Vector3(-10.2, -2.2 + yShift, 2.5 + zShift),
            new THREE.Vector3(-5.2, -2.1 + yShift, -0.5 + zShift),
            new THREE.Vector3(0, -2.0 + yShift, 3.0 + zShift),
            new THREE.Vector3(5.2, -2.1 + yShift, -0.5 + zShift),
            new THREE.Vector3(10.2, -2.2 + yShift, 2.5 + zShift)
        ];
        positions.forEach((pos, i) => {
            const randTemplate = (artifactCounter + i) % ARTIFACT_TEMPLATES.length;
            setTimeout(() => spawnConstellationAt(pos, randTemplate), i * 140);
        });
        artifactCounter += 5;
    }

    let currentHover3D = null;

    function updatePointerRaycast(clientX, clientY, spawnOnMove = true) {
        if (reducedMotion.matches || document.hidden || contextLost) return;
        scene.updateMatrixWorld(true);
        camera.updateMatrixWorld(true);
        mouse.x = (clientX / window.innerWidth) * 2 - 1;
        mouse.y = -(clientY / window.innerHeight) * 2 + 1;

        raycaster.setFromCamera(mouse, camera);
        const intersects = raycaster.intersectObject(invisiblePlane);

        if (intersects.length > 0) {
            const hitPoint = intersects[0].point;
            const localPt = heartParticles.worldToLocal(hitPoint.clone());
            const distFromCenter = Math.sqrt(localPt.x * localPt.x + localPt.z * localPt.z);

            if (distFromCenter < 30.0) {
                currentHover3D = localPt;
                const now = performance.now();
                if (spawnOnMove && now - lastSpawnTime > 380 && hitPoint.distanceTo(lastSpawnPos) > 3.8) {
                    spawnConstellationAt(hitPoint);
                    lastSpawnTime = now;
                    lastSpawnPos.copy(hitPoint);
                }
            } else {
                currentHover3D = null;
            }
        } else {
            currentHover3D = null;
        }
    }

    function onPointerMove(e) {
        updatePointerRaycast(e.clientX, e.clientY);
    }

    function onSceneClick(event) {
        const control = event.target.closest('[data-artifact], [data-artifact-showcase]');
        if (control) {
            if (control.hasAttribute('data-artifact-showcase')) triggerAllShowcase();
            else spawnSpecificArtifact(Number(control.dataset.artifact));
            return;
        }
        if (event.target.closest('a, button, input, select, textarea, label, form')) return;
        updatePointerRaycast(event.clientX, event.clientY, false);
        if (currentHover3D) {
            spawnConstellationAt(heartParticles.localToWorld(currentHover3D.clone()));
        }
    }

    function onWindowResize() {
        camera.aspect = window.innerWidth / window.innerHeight;
        camera.updateProjectionMatrix();
        renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
        renderer.setSize(window.innerWidth, window.innerHeight);
        if (reducedMotion.matches) animate();
    }

    let smoothScroll = 0;

    function animate() {
        if (!reducedMotion.matches && !document.hidden && !contextLost) frame = requestAnimationFrame(animate);

        const dt = reducedMotion.matches ? 0 : Math.min(clock.getDelta(), 0.1);
        const elapsed = reducedMotion.matches ? 0 : clock.elapsedTime;
        smoothScroll += (scrollProgress - smoothScroll) * 0.08;
        camera.position.y = THREE.MathUtils.lerp(10.2, 12.6, smoothScroll);
        camera.position.z = THREE.MathUtils.lerp(23.0, 32.5, smoothScroll);
        const lookAtY = THREE.MathUtils.lerp(0.2, -5.2, smoothScroll);
        const lookAtZ = THREE.MathUtils.lerp(0.0, 10.5, smoothScroll);
        camera.lookAt(0, lookAtY, lookAtZ);
        if (heartGeometry) {
            const posAttr = heartGeometry.attributes.position;
            const colAttr = heartGeometry.attributes.color;
            const posArr = posAttr.array;
            const colArr = colAttr.array;
            const count = posAttr.count;

            for (let i = 0; i < count; i++) {
                const ix = i * 3;
                const iy = ix + 1;
                const iz = ix + 2;

                const bx = heartBasePositions[ix];
                const by = heartBasePositions[iy];
                const bz = heartBasePositions[iz];

                const waveY = Math.sin(bx * 0.22 + elapsed * 1.7) * 0.24
                            + Math.cos(bz * 0.22 + elapsed * 1.4) * 0.20;

                let targetX = bx;
                let targetY = by + waveY;
                let targetZ = bz;
                let lightMul = 0.56;

                if (currentHover3D) {
                    const dx = bx - currentHover3D.x;
                    const dz = bz - currentHover3D.z;
                    const distSq = dx * dx + dz * dz;
                    if (distSq < 42.0) {
                        const dist = Math.sqrt(distSq);
                        const norm = 1 - dist / 6.48;
                        lightMul += norm * norm * 1.85;

                        if (distSq < 18.0) {
                            const lift = 1 - dist / 4.24;
                            targetY += lift * 1.65;
                        }
                    }
                }

                posArr[ix] += (targetX - posArr[ix]) * 0.14;
                posArr[iy] += (targetY - posArr[iy]) * 0.14;
                posArr[iz] += (targetZ - posArr[iz]) * 0.14;

                const glowBoost = lightMul > 1.05 ? (lightMul - 1.05) : 0;
                const targetR = Math.min(1.0, heartBaseColors[ix] * lightMul + glowBoost * 0.35);
                const targetG = Math.min(1.0, heartBaseColors[iy] * lightMul + glowBoost * 0.22);
                const targetB = Math.min(1.0, heartBaseColors[iz] * lightMul + glowBoost * 0.38);

                colArr[ix] += (targetR - colArr[ix]) * 0.16;
                colArr[iy] += (targetG - colArr[iy]) * 0.16;
                colArr[iz] += (targetB - colArr[iz]) * 0.16;
            }
            posAttr.needsUpdate = true;
            colAttr.needsUpdate = true;
        }

        if (heartParticles) {
            heartParticles.parent.rotation.y = Math.sin(elapsed * 0.18) * 0.1;
        }
        for (let i = activeConstellations.length - 1; i >= 0; i--) {
            const item = activeConstellations[i];
            item.age += dt;
            const progress = item.age / item.maxLife;

            if (progress >= 1) {
                removeConstellation(item);
                activeConstellations.splice(i, 1);
                continue;
            }
            const gatherLinear = Math.min(progress / 0.38, 1.0);
            const ease = 1 - Math.pow(1 - gatherLinear, 3);

            const pArr = item.pointsGeo.attributes.position.array;
            const totalPts = pArr.length / 3;

            for (let p = 0; p < totalPts; p++) {
                const idx = p * 3;
                const sx = item.startCoords[idx];
                const sy = item.startCoords[idx + 1];
                const sz = item.startCoords[idx + 2];

                const tx = item.targetCoords[idx];
                const ty = item.targetCoords[idx + 1];
                const tz = item.targetCoords[idx + 2];
                const swirlAmt = (1 - ease) * item.swirlSeeds[p];
                const angle = gatherLinear * Math.PI * 1.8 + item.swirlSeeds[p];

                pArr[idx]     = THREE.MathUtils.lerp(sx, tx, ease) + Math.cos(angle) * swirlAmt * 0.45;
                pArr[idx + 1] = THREE.MathUtils.lerp(sy, ty, ease);
                pArr[idx + 2] = THREE.MathUtils.lerp(sz, tz, ease) + Math.sin(angle) * swirlAmt * 0.45;
            }
            item.pointsGeo.attributes.position.needsUpdate = true;
            const lArr = item.lineGeo.attributes.position.array;
            const pairs = item.softSegmentPairs;
            for (let e = 0; e < pairs.length; e++) {
                const n1 = pairs[e][0] * 3;
                const n2 = pairs[e][1] * 3;
                const base = e * 6;
                lArr[base]     = pArr[n1];
                lArr[base + 1] = pArr[n1 + 1];
                lArr[base + 2] = pArr[n1 + 2];
                lArr[base + 3] = pArr[n2];
                lArr[base + 4] = pArr[n2 + 1];
                lArr[base + 5] = pArr[n2 + 2];
            }
            item.lineGeo.attributes.position.needsUpdate = true;
            item.group.position.y = item.baseY + Math.sin(elapsed * 2.2 + i) * 0.1;
            item.group.rotation.y = Math.sin(elapsed * 0.8 + i) * 0.22;
            let fadeOut = 1.0;
            if (progress > 0.74) fadeOut = 1 - (progress - 0.74) / 0.26;

            item.pointsMat.opacity = Math.min(progress / 0.08, 1.0) * fadeOut * 0.96;
            const lineReveal = THREE.MathUtils.clamp((gatherLinear - 0.65) / 0.35, 0, 1);
            item.lineMat.opacity = lineReveal * fadeOut * 0.20;


        }

        renderer.render(scene, camera);
    }


    function mountArtifactControls() {
        const controls = document.querySelector('[data-artifact-controls]');
        ARTIFACT_TEMPLATES.forEach((template, index) => {
            const button = document.createElement('button');
            button.type = 'button';
            button.dataset.artifact = index;
            button.setAttribute('aria-label', `播放粒子动画 ${index + 1}`);
            const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
            svg.setAttribute('viewBox', '-1.5 -1.5 3 3');
            svg.setAttribute('aria-hidden', 'true');
            for (const stroke of template.strokes) {
                const line = document.createElementNS('http://www.w3.org/2000/svg', 'polyline');
                line.setAttribute('points', stroke.map(([x, y]) => `${x},${-y}`).join(' '));
                svg.appendChild(line);
            }
            button.appendChild(svg);
            controls.appendChild(button);
        });
        const showcase = document.createElement('button');
        showcase.type = 'button';
        showcase.dataset.artifactShowcase = '';
        showcase.setAttribute('aria-label', '播放组合粒子动画');
        const symbol = document.createElement('span');
        symbol.setAttribute('aria-hidden', 'true');
        symbol.textContent = '✦';
        showcase.appendChild(symbol);
        controls.appendChild(showcase);
    }

    function updateAnimation() {
        cancelAnimationFrame(frame);
        const controls = document.querySelector('[data-artifact-controls]');
        controls.hidden = reducedMotion.matches || contextLost;
        if (document.hidden || contextLost) {
            clock.stop();
            return;
        }
        if (reducedMotion.matches) {
            activeConstellations.forEach(removeConstellation);
            activeConstellations = [];
            currentHover3D = null;
            scrollProgress = smoothScroll = 0;
            document.querySelector('.landing-sky').style.removeProperty('--ring-top');
            heartGeometry.attributes.position.array.set(heartBasePositions);
            heartGeometry.attributes.position.needsUpdate = true;
            clock.stop();
        } else {
            onWindowScroll();
            clock.start();
        }
        animate();
    }

    initThreeScene();
}

function initScrollReveal() {
    if (!('IntersectionObserver' in window)) return;
    const observer = new IntersectionObserver(entries => {
        for (const entry of entries) {
            if (entry.isIntersecting) {
                entry.target.classList.add('is-visible');
                observer.unobserve(entry.target);
            }
        }
    }, { threshold: 0.12 });
    for (const card of document.querySelectorAll('.track-card, .learning-card, .update-card, .closing-section')) {
        if (card.getBoundingClientRect().top < window.innerHeight) continue;
        card.classList.add('reveal-on-scroll');
        observer.observe(card);
    }
}

if (document.querySelector('#canvas-container')) {
    initScrollReveal();
    mountLandingScene();
}
